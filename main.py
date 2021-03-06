"""Creating histograms with GPUs.

This module can be used for testing `gpu_hist.py`. You may create histograms
and plot them or you may create a full test. A more detailed description
can be found here: https://github.com/PolygonAndPixel/histogramming/wiki

Examples:
    Create a 2D histogram and compare it with all approaches and with
    histogramdd:

        $ python main.py --dims 2 --cpu --cpu-both --outdir dir

    Make a full test and save the results as *.csv and create plots:

        $ python main.py --test --outdir dir

authors: M. Hieronymus (mhierony@students.uni-mainz.de)
date:    February 2017
"""

from argparse import (ArgumentParser, RawTextHelpFormatter)
from collections import OrderedDict
from copy import deepcopy
from itertools import product
import os
import random as rnd
import sys
from timeit import default_timer as timer
import warnings

import matplotlib
matplotlib.use('agg')
import matplotlib.gridspec as gridspec
import matplotlib.lines as mlines
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.ticker import FormatStrFormatter
import numpy as np
import pandas as pd
import pycuda.autoinit
import pycuda.driver as cuda

import gpu_hist
from gpu_hist import create_array, create_weights, create_edges

FTYPE = np.float64

def mkdir(directory, mode=0o750, warn=True):
    """Simple wrapper around os.makedirs to create a directory but not raise an
    exception if the dir already exists

    Parameters
    ----------
    d : string
        Directory path
    mode : integer
        Permissions on created directory; see os.makedirs for details.
    warn : bool
        Whether to warn if directory already exists.

    """
    try:
        os.makedirs(directory, mode=mode)
    except OSError as err:
        if err.errno == 17:
            if warn:
                print('Directory "%s" already exists' %directory)
        else:
            print("Problem creating directory:", err)
    else:
        print('Created directory "%s"' %directory)


def record_timing(method, info, timings):
    """Save the timings into an ordered dictionary. This can be parsed
    to pandas own dataformat."""
    new_info = deepcopy(info)
    new_info['method'] = method
    new_info['n_trials'] = len(timings)
    new_info['time_median'] = np.median(timings)
    new_info['time_mean'] = np.mean(timings)
    new_info['time_min'] = np.min(timings)
    new_info['time_max'] = np.max(timings)
    new_info['time_std'] = np.std(timings)
    return new_info


def plot_histogram(histogram, edges, outdir, name, no_of_bins):
    """Plots the histogram into specified directory. If the path does not exist
    then it will be created.

    Parameters
    ----------
    histogram : array
    edges : array
    outdir : path
    name : string
    no_of_bins : int (length of edges if edges is given)
    """

    path = [outdir]
    mkdir(os.path.join(*path), warn=False)
    fig = plt.figure()
    ax = fig.add_subplot(111)
    ax.grid(b=True, which='major')
    ax.grid(b=True, which='minor', linestyle=':')

    if len(np.shape(histogram)) == 1:
        width = 60
        if edges is None:
            edges = np.arange(-360, 360, (720/no_of_bins))
            ax.bar(edges, histogram, width)
            ax.set_xticks(edges)
            ax.set_xticklabels(edges)
        else:
            ax.bar(edges[0][0:no_of_bins], histogram, width)
            ax.set_xticks(edges[0])
            ax.set_xticklabels(edges[0])
        ax.xaxis.set_major_formatter(FormatStrFormatter('%.2f'))
        for tick in ax.xaxis.get_major_ticks():
            tick.label.set_fontsize(9)
        for tick in ax.yaxis.get_major_ticks():
            tick.label.set_fontsize(9)
        fig.savefig(outdir+"/"+name)
    elif len(np.shape(histogram)) == 2:
        X, Y = np.meshgrid(edges[0], edges[1])
        plt.pcolormesh(X, Y, np.swapaxes(histogram, 0, 1), cmap='rainbow')
        cbar = plt.colorbar(orientation='vertical')
        cbar.ax.tick_params(labelsize=9)
        ax.set_xticks(edges[0])
        ax.set_yticks(edges[1])
        ax.xaxis.set_major_formatter(FormatStrFormatter('%.2f'))
        ax.yaxis.set_major_formatter(FormatStrFormatter('%.2f'))
        for tick in ax.xaxis.get_major_ticks():
            tick.label.set_fontsize(9)
        for tick in ax.yaxis.get_major_ticks():
            tick.label.set_fontsize(9)
        # set the limits of the image
        plt.axis([X[0][0], X[0][len(X[0])-1], Y[0][0],
                  Y[len(Y)-1][len(Y[len(Y)-1])-1]])
        fig.savefig(outdir+"/"+name)
    elif len(np.shape(histogram)) == 3:
        fig = plt.figure()
        n_histograms = (len(edges[2])-1)/2
        if (len(edges[2])-1)%2 != 0:
            n_histograms = n_histograms+1
        # histogram[x][y][z] -> [z][y][x]
        histogram = np.swapaxes(histogram, 0, 2)
        for i in range(0, len(histogram)):
            title = ('z: ' + '{:06.2f}'.format(edges[2][i]) + " to "
                     + '{:06.2f}'.format(edges[2][i+1]))
            ax = fig.add_subplot(n_histograms, 2, i+1)
            ax.set_title(title, fontsize=9)
            ax.grid(b=True, which='major')
            ax.grid(b=True, which='minor', linestyle=':')
            X, Y = np.meshgrid(edges[0], edges[1])
            tmp_histogram = histogram[i][:][:]
            plt.pcolormesh(X, Y, tmp_histogram, cmap='rainbow')
            cbar = plt.colorbar(orientation='vertical')
            cbar.ax.tick_params(labelsize=9)
            ax.set_xticks(edges[0])
            ax.set_yticks(edges[1])
            ax.xaxis.set_major_formatter(FormatStrFormatter('%.2f'))
            ax.yaxis.set_major_formatter(FormatStrFormatter('%.2f'))
            for tick in ax.xaxis.get_major_ticks():
                tick.label.set_fontsize(9)
            for tick in ax.yaxis.get_major_ticks():
                tick.label.set_fontsize(9)
            # set the limits of the image
            plt.axis([X[0][0], X[0][len(X[0])-1], Y[0][0], Y[len(Y)-1][len(Y[len(Y)-1])-1]])
        fig.tight_layout()
        fig.savefig(outdir+"/"+name)
    else:
        print("Plots are only availale for 3 or less dimensions. Aborting")


def plot_timings(df, outdir, name):
    """Print the timings from --test.
    timings have following order:
    timings [dimensions] 1 to 4
            [n_elements] 10e3 to 10e6
            [bins] 5, 50 and 500
            [precision] single_precision, double_precision
            [Code] CPU, GPU_global, GPU_shared
    """
    path = [outdir]
    mkdir(os.path.join(*path), warn=False)
    width = 1.0
    n_dims = df['n_dims'].max()
    min_dims = df['n_dims'].min()
    n_bins = np.log10(df['n_bins'].max())
    all_bins = np.logspace(1, n_bins, n_bins, dtype=int)
    preallocated = [True, False]
    given_edges = [True, False]

    # Loop over: Preallocated memory or not
    for p in preallocated:
        # For each dimension
        for d in xrange(min_dims, n_dims+1):
            # Count how many rows of plots have been created
            # For each bin_number
            for b in all_bins:
                # We start with single precision and subject to number of elements
                # We compare the speed with given edges and without
                fig = plt.figure()
                no_subplots = True
                gs = gridspec.GridSpec(4, 2, width_ratios=[1, 1],
                                       height_ratios=[0.5, 40, 40, 0.1])
                if p:
                    plot_title = ('Histogram: Speedup and runtime with CPU and'
                                  ' GPU (' + str(d) + 'D)\n'
                                  'using already allocated device arrays')
                else:
                    plot_title = ('Histogram: Speedup and runtime with CPU and'
                                  ' GPU (' + str(d) + 'D)')
                plt.suptitle(plot_title, fontsize=16)

                for e in given_edges:
                    # plots x-axis: n_elements, y_axis1: timings, y_axis2: speedup
                    if e:
                        ax_f = plt.subplot(gs[4])
                    else:
                        ax_f = plt.subplot(gs[2])
                    seq_time_f = (df.loc[(df['method'] == 'cpu')
                                         & (df['ftype'] == 'float32')
                                         & (df['n_dims'] == d)
                                         & (df['n_bins'] == b)
                                         & (df['given_edges'] == e)
                                         & (df['device_samples'] == p)]
                                  ['time_mean'].tolist())
                    time_global_f = (df.loc[(df['method'] == 'gpu_global')
                                            & (df['ftype'] == 'float32')
                                            & (df['n_dims'] == d)
                                            & (df['n_bins'] == b)
                                            & (df['given_edges'] == e)
                                            & (df['device_samples'] == p)]
                                     ['time_mean'].tolist())
                    time_shared_f = (df.loc[(df['method'] == 'gpu_shared')
                                            & (df['ftype'] == 'float32')
                                            & (df['n_dims'] == d)
                                            & (df['n_bins'] == b)
                                            & (df['given_edges'] == e)
                                            & (df['device_samples'] == p)]
                                     ['time_mean'].tolist())
                    n_elements_f = (df.loc[(df['method'] == 'cpu')
                                           & (df['ftype'] == 'float32')
                                           & (df['n_dims'] == d)
                                           & (df['n_bins'] == b)
                                           & (df['given_edges'] == e)
                                           & (df['device_samples'] == p)]
                                    ['n_elements'].tolist())
                    if seq_time_f:
                        no_subplots = False
                        create_subfig(seq_time_f, time_global_f,
                                      time_shared_f,
                                      np.asarray(n_elements_f), ax_f,
                                      width, 'Number of elements', '(SP)', e, b)
                    # Next double precision
                    # plots x-axis: n_elements, y_axis1: timings, y_axis2: speedup
                    if e:
                        ax_d = plt.subplot(gs[5])
                    else:
                        ax_d = plt.subplot(gs[3])
                    seq_time_d = (df.loc[(df['method'] == 'cpu')
                                         & (df['ftype'] == 'float64')
                                         & (df['n_dims'] == d)
                                         & (df['n_bins'] == b)
                                         & (df['given_edges'] == e)
                                         & (df['device_samples'] == p)]
                                  ['time_mean'].tolist())
                    time_global_d = (df.loc[(df['method'] == 'gpu_global')
                                            & (df['ftype'] == 'float64')
                                            & (df['n_dims'] == d)
                                            & (df['n_bins'] == b)
                                            & (df['given_edges'] == e)
                                            & (df['device_samples'] == p)]
                                     ['time_mean'].tolist())
                    time_shared_d = (df.loc[(df['method'] == 'gpu_shared')
                                            & (df['ftype'] == 'float64')
                                            & (df['n_dims'] == d)
                                            & (df['n_bins'] == b)
                                            & (df['given_edges'] == e)
                                            & (df['device_samples'] == p)]
                                     ['time_mean'].tolist())
                    n_elements_d = (df.loc[(df['method'] == 'cpu')
                                           & (df['ftype'] == 'float64')
                                           & (df['n_dims'] == d)
                                           & (df['n_bins'] == b)
                                           & (df['given_edges'] == e)
                                           & (df['device_samples'] == p)]
                                    ['n_elements'].tolist())
                    if seq_time_d:
                        no_subplots = False
                        create_subfig(seq_time_d, time_global_d,
                                      time_shared_d,
                                      np.asarray(n_elements_d), ax_d,
                                      width, 'Number of elements', '(DP)', e, b)

                    with warnings.catch_warnings():
                        # This raises warnings since tight layout cannot
                        # handle gridspec automatically. We are going to
                        # do that manually so we can filter the warning.
                        if seq_time_d:
                            warnings.simplefilter("ignore", UserWarning)
                            gs.tight_layout(fig)
                if p:
                    fig_name = (outdir+"/n_dims_"+str(d)+"_n_bins_"+str(b)
                                +"_with-device-samples_"+name)
                else:
                    fig_name = (outdir+"/n_dims_"+str(d)+"_n_bins_"+str(b)
                                +"_"+name)
                if not no_subplots:
                    plt.savefig(fig_name, dpi=600)
                plt.close(fig)


def create_subfig(seq_time1, running_time1_global, running_time1_shared,
                  n_elements, ax1, width, x_name, title, given_edges, amount):
    """
    This method is called from plot_timings(). Subplots with timings and
    speedup are created. It handles the annotations and formatting.
    """
    if given_edges:
        plot_title = (title + " with " + "{:.0E}".format(amount) + " bins\n"
                      + "and given edges")
    else:
        plot_title = (title + " with " + "{:.0E}".format(amount) + " bins\n"
                      + "and no given edges")
    ax1.set_title(plot_title, fontsize=10)
    ax1.grid(b=True, which='major')
    ax1.xaxis.set_major_formatter(FormatStrFormatter('%.2f'))
    ax1.yaxis.set_major_formatter(FormatStrFormatter('%.2f'))

    ax1.set_xscale('log')
    ax1.set_yscale('log')
    ax1_speedup = ax1.twinx()
    ax1_speedup.set_yscale('log')
    speedup1_global = []
    for i in range(0, len(running_time1_global)):
        speedup1_global.append(seq_time1[i]/running_time1_global[i])
    speedup1_shared = []
    for i in range(0, len(running_time1_shared)):
        speedup1_shared.append(seq_time1[i]/running_time1_shared[i])
    width_list = []
    for n in n_elements:
        width_list.append(n/3 * width)
    ax1_speedup.plot(n_elements, speedup1_global,
                     color='black', marker="x",
                     label='Speedup with global memory')
    ax1_speedup.plot(n_elements, speedup1_shared,
                     color='black', linestyle='--',
                     marker="o", fillstyle='none',
                     label='Speedup with shared memory')
    ax1.bar(n_elements-width_list, running_time1_global,
            width=width_list, color=(0.7, 0.7, 0.8), align='edge',
            label="GPU global memory")
    ax1.bar(n_elements, running_time1_shared, width=width_list,
            color=(0.4, 0.4, 0.8), align='edge', label="GPU shared memory")
    ax1.bar(n_elements+width_list, seq_time1,
            width=width_list, color=(0.4, 0.7, 0.8), align='edge',
            label="CPU")
    global_approach = mpatches.Patch(color=(0.7, 0.7, 0.8),
                                     label='GPU global memory')
    shared_approach = mpatches.Patch(color=(0.4, 0.4, 0.8),
                                     label='GPU shared memory')
    cpu_approach = mpatches.Patch(color=(0.4, 0.7, 0.8), label='CPU')
    speed_up_global = mlines.Line2D([], [], color='black', marker="x",
                                    linestyle='-',
                                    label='Speedup with global memory')
    speed_up_shared = mlines.Line2D([], [], color='black', linestyle='--',
                                    marker="o", fillstyle='none',
                                    label='Speedup with shared memory')
    plt.legend(handles=[global_approach, shared_approach, cpu_approach,
                        speed_up_global, speed_up_shared],
               bbox_to_anchor=(0.5, 0.0), loc=8,
               bbox_transform=plt.gcf().transFigure, ncol=3,
               fontsize=10)
    ax1.set_xlabel(x_name, fontsize=8)
    ax1.set_ylabel('Running time in seconds', fontsize=8)
    ax1_speedup.set_ylabel('Speedup compared to CPU version', fontsize=8)
    for tick in ax1.xaxis.get_major_ticks():
        tick.label.set_fontsize(9)
    for tick in ax1.yaxis.get_major_ticks():
        tick.label.set_fontsize(9)
    for label in ax1_speedup.yaxis.get_majorticklabels():
        label.set_fontsize(9)
    plt.xlim(n_elements[0]-width_list[0]*2,
             n_elements[len(n_elements)-1]+width_list[len(width_list)-1]*2)


if __name__ == '__main__':
    """
    This is based on
    https://devblogs.nvidia.com/parallelforall/gpu-pro-tip-fast-histograms-using-shared-atomics-maxwell/
    http://parse.ele.tue.nl/system/attachments/10/original/GPGPU_High%20Performance%20Predictable%20Histogramming%20on%20GPUs.pdf?1314781744
    https://isaac.gelado.cat/sites/isaac.gelado.cat/files/publications/samos_2013_histogramming.pdf
    """
    # Do cool stuff
    parser = ArgumentParser(
        description='''Run several tests for histogramming with a GPU.''',
        formatter_class=RawTextHelpFormatter)
    parser.add_argument(
        '--full', action='store_true',
        help=
        '''Full test with comparison of numpy's histogramdd and GPU code
        with single and double precision and the GPU code with shared and
        global memory.''')
    parser.add_argument(
        '--gpu-shared', action='store_true',
        help=
        '''Use GPU code with shared memory. If --gpu-both is set, then
        --gpu-shared will be ignored.''')
    parser.add_argument(
        '--gpu-global', action='store_true',
        help=
        '''Use GPU code with global memory. If --gpu-both is set, then
        --gpu-global will be ignored.''')
    parser.add_argument(
        '--gpu-both', action='store_true',
        help=
        '''Use GPU code with shared memory and global memory and compare
        both.''')
    parser.add_argument(
        '--cpu', action='store_true',
        help=
        '''Use numpy's histogramdd.''')
    parser.add_argument(
        '--all-precisions', action='store_true',
        help=
        '''Run all specified tests with double and single precision.''')
    parser.add_argument(
        '-s', '--single-precision', action='store_true', help=
        '''Use single precision. If it is not set, use double precision.
        If --all-precisions is used, then -s will be ignored.''')
    parser.add_argument(
        '-d', '--data', type=int, required=False,
        default=256*256, help=
        '''Define the number of elements in each dimension for the input
        data.''')
    parser.add_argument(
        '--device-data', action='store_true',
        help=
        '''Use device arrays as input data.''')
    parser.add_argument(
        '--list-data', action='store_true',
        help=
        '''Create a list of device arrays as input data. Each array contains
        one dimension of all samples. This can be done for 1, 2 and 3
        dimensions. This is overwritten by --device-data and it is ignored
        if --dims is higher than 3.''')
    parser.add_argument(
        '--dims', type=int, required=False, default=1,
        help=
        '''Define the number of dimensions for the input data and
        the histogram.''')
    parser.add_argument(
        '-b', '--bins', type=int, required=False, default=256,
        help=
        '''Choose the number of bins for each dimension''')
    parser.add_argument(
        '-w', '--weights', action='store_true',
        help=
        '''(Randomized) weights will be used on the histogram.''')
    parser.add_argument(
        '--use-given-edges', action='store_true',
        help=
        '''Use calculated edges instead of calculating edges during
        histogramming.''')
    parser.add_argument(
        '--use-irregular-edges', action='store_true',
        help=
        '''The number of edges varies with number of bins/2 for each
        dimension.
        The mean should be at least 6 bins for each dimension.''')
    parser.add_argument(
        '--outdir', metavar='DIR', type=str,
        help=
        '''Store all output plots to this directory. If
        they don't exist, the script will make them, including
        all subdirectories. If none is supplied no plots will
        be saved.''')
    parser.add_argument(
        '--test', action='store_true',
        help=
        '''Make a test with all versions and create plots to the directory
        given with `--outdir`''')
    args = parser.parse_args()
    ftype = np.float64
    if args.single_precision and not args.all_precisions and not args.full:
        ftype = np.float32

    if args.outdir is not None:
        mkdir(args.outdir, warn=False)

    weights = None
    d_weights = None
    if args.weights:
        weights, d_weights = create_weights(n_elements=args.data,
                                            ftype=ftype,
                                            device_array=(args.device_data or
                                                          args.list_data))

    input_data, d_input_data = create_array(n_elements=args.data,
                                            n_dims=args.dims,
                                            device_array=args.device_data,
                                            ftype=ftype,
                                            list_array=args.list_data)
    edges = None
    if args.use_given_edges:
        edges = create_edges(n_bins=args.bins, n_dims=args.dims,
                             random=args.use_irregular_edges, ftype=ftype)

    if edges is None and args.use_irregular_edges:
        if args.bins < 6:
            args.bins = 6
        edges = []
        for i in range(0, args.dims):
            edges.append(rnd.randint(args.bins/2, 3*args.bins/2))
    elif edges is None:
        edges = args.bins

    if args.test:
        n_trials = 10
        timings = []

        all_dims = [1, 2, 3]
        all_elements = np.logspace(5, 9, 5)
        all_bins = np.logspace(1, 4, 4)
        all_ftypes = [np.float32, np.float64]
        all_device_samples = [False, True]
        all_given_edges = [False, True]
        gpu_attributes = cuda.Device(0).get_attributes()
        max_threads_per_block = gpu_attributes.get(
            cuda.device_attribute.MAX_THREADS_PER_BLOCK
        )

        for n_dims, n_elements, n_bins, ftype, device_samples, given_edges in product(
                all_dims, all_elements, all_bins, all_ftypes,
                all_device_samples, all_given_edges):
            n_elements = int(n_elements)
            n_bins = int(n_bins)
            # Check if everything fits on the GPU. Continue if it is not the case.
            # One integer is 4 bytes. We need to know how many blocks there are
            # with their own histogram. We also take the samples into account
            # and the edges if they are given and need to be copied.
            dx, mx = divmod(n_elements, max_threads_per_block)
            grid_dim = dx + (mx > 0)
            # local histograms
            n_bytes = n_bins**n_dims*grid_dim*4

            if ftype == np.float32:
                # samples
                n_bytes += n_dims*n_elements*4
                if given_edges:
                    n_bytes += 4*n_bins**n_dims
            else:
                # samples
                n_bytes += n_dims*n_elements*8
                if given_edges:
                    n_bytes += 8*n_bins**n_dims
            available_memory = cuda.mem_get_info()[0]
            if n_bytes > available_memory:
                continue

            info = OrderedDict([
                ('ftype', ftype.__name__),
                ('n_dims', n_dims),
                ('n_elements', n_elements),
                ('n_bins', n_bins),
                ('device_samples', device_samples),
                ('given_edges', given_edges)
            ])

            # CPU
            tmp_timings = []
            for i in xrange(n_trials):
                # Create test data inside the loop to avoid caching
                input_data, d_input_data = create_array(
                    n_elements=n_elements,
                    n_dims=n_dims,
                    device_array=False,
                    ftype=ftype,
                    list_array=args.list_data
                )
                edges = None
                if given_edges:
                    edges = create_edges(n_bins=n_bins, n_dims=n_dims,
                                         random=False, ftype=ftype)
                else:
                    edges = n_bins

                start = timer()
                histogram_d_numpy, edges_d = np.histogramdd(
                    input_data, bins=edges, weights=weights
                )
                end = timer()
                tmp_timings.append(end - start)
                if isinstance(d_input_data, cuda.DeviceAllocation):
                    d_input_data.free()
            timings.append(
                record_timing(method='cpu', info=info, timings=tmp_timings)
            )

            # GPU global memory
            tmp_timings = []
            with gpu_hist.GPUHist(ftype=ftype) as histogrammer:
                for i in xrange(n_trials):
                    # Create test data inside the loop to avoid caching
                    input_data, d_input_data = create_array(
                        n_elements=n_elements,
                        n_dims=n_dims,
                        device_array=device_samples,
                        ftype=ftype,
                        list_array=args.list_data
                    )
                    edges = None
                    if given_edges:
                        edges = create_edges(n_bins=n_bins, n_dims=n_dims,
                                             random=False, ftype=ftype)
                    else:
                        edges = n_bins

                    start = timer()
                    histogram_gpu_global, edges_gpu_global = histogrammer.get_hist(
                        sample=d_input_data, bins=edges, shared=False,
                        dims=n_dims, number_of_events=n_elements
                    )
                    end = timer()
                    tmp_timings.append(end - start)
                    if isinstance(d_input_data, cuda.DeviceAllocation):
                        d_input_data.free()
            timings.append(
                record_timing(method='gpu_global', info=info, timings=tmp_timings)
            )

            # GPU shared memory
            tmp_timings = []
            with gpu_hist.GPUHist(ftype=ftype) as histogrammer:
                for i in xrange(n_trials):
                    # Create test data inside the loop to avoid caching
                    input_data, d_input_data = create_array(
                        n_elements=n_elements,
                        n_dims=n_dims,
                        device_array=device_samples,
                        ftype=ftype,
                        list_array=args.list_data
                    )
                    edges = None
                    if given_edges:
                        edges = create_edges(n_bins=n_bins, n_dims=n_dims,
                                             random=False, ftype=ftype)
                    else:
                        edges = n_bins

                    start = timer()
                    histogram_gpu_shared, edges_gpu_shared = histogrammer.get_hist(
                        sample=d_input_data, bins=edges, shared=True,
                        dims=n_dims, number_of_events=n_elements
                    )
                    end = timer()
                    tmp_timings.append(end - start)
                    if isinstance(d_input_data, cuda.DeviceAllocation):
                        d_input_data.free()
            timings.append(
                record_timing(method='gpu_shared', info=info, timings=tmp_timings)
            )
        name = "Speedup_test_"
        df = pd.DataFrame(timings)
        df.sort_values(by=['ftype', 'n_dims', 'n_elements', 'n_bins',
                           'method'], inplace=True)
        pd.set_option('display.max_rows', 500)
        pd.set_option('display.max_columns', 500)
        pd.set_option('display.width', 1000)
        print(df)
        if args.outdir is not None:
            df.to_csv(os.path.join(args.outdir, name + '.csv'))
            plot_timings(df, args.outdir, name)
        sys.exit()

    if args.full:
        # First with double precision
        with gpu_hist.GPUHist(ftype=ftype) as histogrammer:
            histogram_d_gpu_shared, edges_d_gpu_shared = histogrammer.get_hist(
                sample=d_input_data, bins=edges, weights=d_weights, shared=True,
                dims=args.dims, number_of_events=args.data
            )
            histogram_d_gpu_global, edges_d_gpu_global = histogrammer.get_hist(
                sample=d_input_data, bins=edges, weights=d_weights, shared=False,
                dims=args.dims, number_of_events=args.data
            )
        if edges is None:
            histogram_d_numpy, edges_d = np.histogramdd(input_data,
                                                        bins=args.bins,
                                                        weights=weights)
        else:
            histogram_d_numpy, edges_d = np.histogramdd(input_data,
                                                        bins=edges,
                                                        weights=weights)
        # Next with single precision
        ftype = np.float32
        input_data, d_input_data = create_array(
            n_elements=args.data,
            n_dims=args.dims,
            device_array=args.device_data,
            ftype=ftype,
            list_array=args.list_data
        )
        with gpu_hist.GPUHist(ftype=ftype) as histogrammer:
            histogram_s_gpu_shared, edges_s_gpu_shared = histogrammer.get_hist(
                sample=d_input_data, bins=edges, weights=d_weights, shared=True,
                dims=args.dims, number_of_events=args.data
            )
            histogram_s_gpu_global, edges_s_gpu_global = histogrammer.get_hist(
                sample=d_input_data, bins=edges, weights=d_weights, shared=False,
                dims=args.dims, number_of_events=args.data
            )
        if edges is None:
            histogram_s_numpy, edges_s = np.histogramdd(input_data,
                                                        bins=args.bins,
                                                        weights=weights)
        else:
            histogram_s_numpy, edges_s = np.histogramdd(input_data,
                                                        bins=edges,
                                                        weights=weights)
        if args.outdir != None:
            plot_histogram(histogram_d_gpu_shared, edges_d_gpu_shared,
                           args.outdir, "GPU shared memory, double", args.bins)
            plot_histogram(histogram_d_gpu_global, edges_d_gpu_global,
                           args.outdir, "GPU global memory, double", args.bins)
            plot_histogram(histogram_d_numpy, edges_d, args.outdir,
                           "CPU, double", args.bins)
            plot_histogram(histogram_s_gpu_shared, edges_s_gpu_shared,
                           args.outdir, "GPU shared memory, single", args.bins)
            plot_histogram(histogram_s_gpu_global, edges_s_gpu_global,
                           args.outdir, "GPU global memory, single", args.bins)
            plot_histogram(histogram_s_numpy, edges_s, args.outdir,
                           "CPU, single", args.bins)
        sys.exit()

    if args.gpu_both:
        # if not args.all_precisions and args.single_precision then this is
        # single precision. Hence the missing "d" or "s" in the name.
        with gpu_hist.GPUHist(ftype=ftype) as histogrammer:
            histogram_gpu_shared, edges_gpu_shared = histogrammer.get_hist(
                sample=d_input_data, bins=edges, weights=d_weights, shared=True,
                dims=args.dims, number_of_events=args.data
            )
        with gpu_hist.GPUHist(ftype=ftype) as histogrammer:
            histogram_gpu_global, edges_gpu_global = histogrammer.get_hist(
                sample=d_input_data, bins=edges, weights=d_weights, shared=False,
                dims=args.dims, number_of_events=args.data
            )
        if args.all_precisions:
            ftype = np.float32
            input_data, d_input_data = create_array(
                n_elements=args.data,
                n_dims=args.dims,
                device_array=args.device_data,
                ftype=ftype,
                list_array=args.list_data
            )
            with gpu_hist.GPUHist(ftype=ftype) as histogrammer:
                histogram_s_gpu_shared, edges_s_gpu_shared = histogrammer.get_hist(
                    sample=d_input_data, bins=edges, weights=d_weights, shared=True,
                    dims=args.dims, number_of_events=args.data
                )
                histogram_s_gpu_global, edges_s_gpu_global = histogrammer.get_hist(
                    sample=d_input_data, bins=edges, weights=d_weights, shared=False,
                    dims=args.dims, number_of_events=args.data
                )
            if args.outdir != None:
                plot_histogram(histogram_gpu_shared, edges_gpu_shared,
                               args.outdir, "GPU shared memory, double",
                               args.bins)
                plot_histogram(histogram_gpu_global, edges_gpu_global,
                               args.outdir, "GPU global memory, double",
                               args.bins)
                plot_histogram(histogram_s_gpu_shared, edges_s_gpu_shared,
                               args.outdir, "GPU shared memory, single",
                               args.bins)
                plot_histogram(histogram_s_gpu_global, edges_s_gpu_global,
                               args.outdir, "GPU global memory, single",
                               args.bins)
        elif args.outdir != None:
            name = ""
            if args.single_precision:
                name = "single"
            else:
                name = "double"
            plot_histogram(histogram_gpu_shared, edges_gpu_shared, args.outdir,
                           "GPU shared memory, " + name, args.bins)
            plot_histogram(histogram_gpu_global, edges_gpu_global, args.outdir,
                           "GPU global memory, " + name, args.bins)

    if args.gpu_shared and not args.gpu_both:
        with gpu_hist.GPUHist(ftype=ftype) as histogrammer:
            histogram_gpu_shared, edges_gpu_shared = histogrammer.get_hist(
                sample=d_input_data, bins=edges, weights=d_weights, shared=True,
                dims=args.dims, number_of_events=args.data
            )
        if args.all_precisions:
            ftype = np.float32
            input_data, d_input_data = create_array(
                n_elements=args.data,
                n_dims=args.dims,
                device_array=args.device_data,
                ftype=ftype,
                list_array=args.list_data
            )
            with gpu_hist.GPUHist(ftype=ftype) as histogrammer:
                histogram_s_gpu_shared, edges_s_gpu_shared = histogrammer.get_hist(
                    sample=d_input_data, bins=edges, weights=d_weights, shared=True,
                    dims=args.dims, number_of_events=args.data
                )
            if args.outdir != None:
                plot_histogram(histogram_gpu_shared, edges_gpu_shared,
                               args.outdir, "GPU shared memory, double",
                               args.bins)
                plot_histogram(histogram_s_gpu_shared, edges_s_gpu_shared,
                               args.outdir, "GPU shared memory, single",
                               args.bins)
        elif args.outdir != None:
            name = ""
            if args.single_precision:
                name = "single"
            else:
                name = "double"
            plot_histogram(histogram_gpu_shared, edges_gpu_shared, args.outdir,
                           "GPU shared memory, " + name, args.bins)

    if args.gpu_global and not args.gpu_both:
        with gpu_hist.GPUHist(ftype=ftype) as histogrammer:
            histogram_gpu_global, edges_gpu_global = histogrammer.get_hist(
                sample=d_input_data, bins=edges, weights=d_weights, shared=False,
                dims=args.dims, number_of_events=args.data
            )
        if args.all_precisions:
            ftype = np.float32
            input_data, d_input_data = create_array(
                n_elements=args.data,
                n_dims=args.dims,
                device_array=args.device_data,
                ftype=ftype,
                list_array=args.list_data
            )
            with gpu_hist.GPUHist(ftype=ftype) as histogrammer:
                histogram_s_gpu_global, edges_s_gpu_global = histogrammer.get_hist(
                    sample=d_input_data, bins=edges, weights=d_weights, shared=False,
                    dims=args.dims, number_of_events=args.data
                )
            if args.outdir != None:
                plot_histogram(histogram_gpu_global, edges_gpu_global,
                               args.outdir, "GPU global memory, double",
                               args.bins)
                plot_histogram(histogram_s_gpu_global, edges_s_gpu_global,
                               args.outdir, "GPU global memory, single",
                               args.bins)
        elif args.outdir != None:
            name = ""
            if args.single_precision:
                name = "single"
            else:
                name = "double"
            plot_histogram(histogram_gpu_global, edges_gpu_global, args.outdir,
                           "GPU global memory, " + name, args.bins)

    if args.cpu:
        if edges is None:
            histogram_d_numpy, edges_d = np.histogramdd(
                input_data,
                bins=args.bins,
                weights=weights
            )
        else:
            histogram_d_numpy, edges_d = np.histogramdd(
                input_data,
                bins=edges,
                weights=weights
            )
        if args.all_precisions:
            ftype = np.float32
            input_data, d_input_data = create_array(
                n_elements=args.data,
                n_dims=args.dims,
                device_array=args.device_data,
                ftype=ftype,
                list_array=args.list_data
            )
            if edges is None:
                histogram_s_numpy, edges_s = np.histogramdd(
                    input_data,
                    bins=args.bins,
                    weights=weights
                )
            else:
                histogram_s_numpy, edges_s = np.histogramdd(
                    input_data,
                    bins=edges,
                    weights=weights
                )
        if args.outdir != None:
            plot_histogram(histogram_d_numpy, edges_d, args.outdir,
                           "CPU, double", args.bins)
            if args.all_precisions:
                plot_histogram(histogram_s_numpy, edges_s, args.outdir,
                               "CPU, single", args.bins)
