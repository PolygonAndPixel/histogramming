# authors: M. Hieronymus (mhierony@students.uni-mainz.de)
# date:    November 2016

import os

import numpy as np
from pycuda.compiler import SourceModule
import pycuda.driver as cuda
import pycuda.autoinit

# from pisa import FTYPE, C_FTYPE, C_PRECISION_DEF # Used in PISA

#TODO: Add more comments. Remove edges array and add multidimensional edge array.
class GPUHist(object):
    """
    Histogramming class for GPUs
    Basic implemention is based on
    https://devblogs.nvidia.com/parallelforall/gpu-pro-tip-fast-histograms-using-shared-atomics-maxwell/
    and modified by M. Hieronymus.

    Parameters
    ----------
    bin_edges_x : array
    bin_edges_y : array
    bin_edges_z : array (optional)
    FTYPE : np.float64 or np.float32
    """

    FTYPE = np.float64
    C_FTYPE = 'double'
    C_PRECISION_DEF = 'DOUBLE_PRECISION'
    C_ITYPE = 'unsigned int'
    C_HIST_TYPE = 'unsigned int'
    HIST_TYPE = np.uint32
    ITYPE = np.uint32

    def __init__(self, no_of_dimensions, no_of_bins, edges=None, FTYPE=np.float64):
        #TODO:  memory, GPU version
        # Set some default types.
        self.FTYPE = FTYPE
        # Might be useful. PISA used it for atomic cuda_utils.h with
        # custom atomic_add for floats and doubles.
        #include_dirs = [os.path.abspath(find_resource('../gpu_hist'))]
        #TODO: Add self.n_flat_bins = product of length of all dimensions
        self.n_flat_bins = no_of_dimensions * no_of_bins
        self.no_of_dimensions = self.ITYPE(no_of_dimensions)
        self.no_of_bins = self.ITYPE(no_of_bins)
        kernel_code = open("gpu_hist/histogram_atomics.cu", "r").read() %dict(
            c_precision_def=self.C_PRECISION_DEF,
            c_ftype=self.C_FTYPE,
            c_itype=self.C_ITYPE,
            c_uitype=self.C_HIST_TYPE,
            n_flat_bins=self.n_flat_bins
        )
        include_dirs = ['/gpu_hist']
        # keep for compiler output, no_extern_c: allow name manling
        module = SourceModule(kernel_code, keep=True,
                options=['--compiler-options','-Wall', '-g'],
                include_dirs=include_dirs, no_extern_c=False)
        #module = SourceModule(kernel_code, include_dirs=include_dirs, keep=True)
        self.max_min_reduce = module.get_function("max_min_reduce")
        self.hist_gmem = module.get_function("histogram_gmem_atomics")
        self.hist_gmem_given_edges = module.get_function("histogram_gmem_atomics_with_edges")
        self.hist_smem = module.get_function("histogram_smem_atomics_with_edges")
        self.hist_accum = module.get_function("histogram_final_accum")
        # We use a one-dimensional block and grid. 16*16
        self.block_dim = (16*16, 1, 1)
        self.d_hist = cuda.mem_alloc(self.n_flat_bins
                * np.dtype(self.HIST_TYPE).itemsize)
        # Define shared memory for max- and min-reduction
        self.shared = (self.block_dim[0] * np.dtype(self.C_FTYPE).itemsize * 2)
        self.edges = edges


    def clear(self):
        """Clear the histogram bins on the GPU"""
        self.hist = np.zeros(self.n_flat_bins, dtype=self.HIST_TYPE)
        cuda.memcpy_htod(self.d_hist, self.hist)


    # TODO: Check if shared memory or not
    def get_hist(self, n_events, shared):
        """Retrive histogram with given events and edges"""
        self.clear()
        # Copy the  arrays
        d_events = cuda.mem_alloc(n_events.nbytes)
        cuda.memcpy_htod(d_events, n_events)

        # Calculate the number of blocks needed
        dx, mx = divmod(len(n_events), self.block_dim[0])
        self.grid_dim = ( (dx + (mx>0)), 1 )

        # Allocate local and final histograms on device
        self.d_tmp_hist = cuda.mem_alloc(self.n_flat_bins * self.grid_dim[0]
                * np.dtype(self.HIST_TYPE).itemsize)

        # Calculate edges by yourself if no edges are given
        if self.edges is None:
            d_max_in = cuda.mem_alloc(self.no_of_dimensions
                    * np.dtype(self.FTYPE).itemsize)
            d_min_in = cuda.mem_alloc(self.no_of_dimensions
                    * np.dtype(self.FTYPE).itemsize)
            self.max_min_reduce(d_events,
                    self.HIST_TYPE(len(n_events)/self.no_of_dimensions),
                    self.no_of_dimensions, d_max_in, d_min_in,
                    block=self.block_dim, grid=self.grid_dim,
                    shared=self.shared)
            #cuda.Context.synchronize()
            self.hist_gmem(d_events, self.HIST_TYPE(len(n_events)),
                    self.no_of_dimensions, self.no_of_bins, self.d_tmp_hist,
                    d_max_in, d_min_in,
                    block=self.block_dim, grid=self.grid_dim,
                    shared=self.shared)
        else:
            d_edges_in = cuda.mem_alloc((self.n_flat_bins+1)
                                        * np.dtype(self.FTYPE).itemsize)
            cuda.memcpy_htod(d_edges_in, self.edges)
            self.hist_gmem_given_edges(d_events,
                    self.HIST_TYPE(len(n_events)), self.no_of_dimensions,
                    self.no_of_bins, self.d_tmp_hist, d_edges_in,
                    block=self.block_dim, grid=self.grid_dim,
                    shared=self.shared)

        # TODO: Check if new dimensions might be useful
        self.hist_accum(self.d_tmp_hist, self.ITYPE(self.grid_dim[0]), self.d_hist,
                self.no_of_bins, self.no_of_dimensions,
                block=self.block_dim, grid=self.grid_dim)
        # Copy the array back
        cuda.memcpy_dtoh(self.hist, self.d_hist)
        if self.edges is None:
            # Calculate the found edges
            max_in = np.zeros(self.no_of_dimensions, dtype=self.FTYPE)
            min_in = np.zeros(self.no_of_dimensions, dtype=self.FTYPE)
            cuda.memcpy_dtoh(max_in, d_max_in)
            cuda.memcpy_dtoh(min_in, d_min_in)
            #TODO; Check return of edges for 2D with numpy's implementation
            self.edges = []
            # Create some nice edges
            for d in range(0, self.no_of_dimensions):
                bin_width = (max_in[d]-min_in[d])/self.no_of_bins
                edges_d =  np.arange(min_in[d], max_in[d]+1, bin_width)
                self.edges.append(edges_d)
            self.edges = np.asarray(self.edges, dtype=self.FTYPE)
        #TODO: Check if device arrays are given
        return self.hist, self.edges


    def set_variables(FTYPE):
        """This method sets some variables like FTYPE and should be called at
        least once before calculating a histogram. Those variables are already
        set in PISA with the commented import from above."""
        if FTYPE == np.float32:
            C_FTYPE = 'float'
            C_PRECISION_DEF = 'SINGLE_PRECISION'
            self.FTYPE = FTYPE
            sys.stderr.write("Histogramming is set to single precision (FP32) "
                    "mode.\n\n")
        elif FTYPE == np.float64:
            C_FTYPE = 'double'
            C_PRECISION_DEF = 'DOUBLE_PRECISION'
            self.FTYPE = FTYPE
            sys.stderr.write("Histogramming is set to double precision (FP64) "
                    "mode.\n\n")
        else:
            raise ValueError('FTYPE must be one of `np.float32` or `np.float64`'
                    '. Got %s instead.' %FTYPE)


    def __enter__(self):
        return self


    def __exit__(self, exc_type, exc_val, exc_tb):
        #self.clear()
        return


def test_GPUHist():
    """A small test which calculates a histogram"""

if __name__ == '__main__':
    test_GPUHist()
