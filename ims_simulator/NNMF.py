#!/usr/bin/env python
# coding: utf-8

from cpyImagingMSpec import ImzbReader
import dask.array as da
import numpy as np
from toolz import partition_all

from mz_axis import generate_mz_axis, Instrument
from external.nnls import nnlsm_blockpivot

import argparse
import sys

#np.random.seed(24)


def nnmf_calculation(arr, arr_idx, rank):
    def nnls_frob(x, anchors):
        ncols = x.shape[1]
        x_sel = np.array(anchors)
        # print "projection"
        result = np.zeros((x_sel.shape[1], ncols))

        # apply NNLS to chunks so as to avoid loading all m/z images into RAM
        for chunk in partition_all(100, range(ncols)):
            residuals = np.array(x[:, chunk])
            result[:, chunk] = nnlsm_blockpivot(x_sel, residuals)[0]

        return result

    arr_pos = np.asarray(arr[arr_idx])
    print arr_pos.shape
    cols = []
    r = rank
    # treat images as vectors (flatten them)
    x = arr_pos.reshape((arr_pos.shape[0], -1)).T

    print "Running non-negative matrix factorization"

    # apply XRay algorithm
    # ('Fast conical hull algorithms for near-separable non-negative matrix factorization' by Kumar et. al., 2012)
    R = x
    while len(cols) < r:
        # print "detection"
        p = np.random.random(x.shape[0])
        scores = (R * x).sum(axis=0)
        scores /= p.T.dot(x)
        scores = np.array(scores)
        scores[cols] = -1
        best_col = np.argmax(scores)
        assert best_col not in cols
        cols.append(best_col)
        print "picked {}/{} columns".format(len(cols), r)

        H = nnls_frob(x, x[:, cols])
        R = x - np.dot(x[:, cols], H)

        if len(cols) > 0 and len(cols) % 5 == 0:
            residual_error = np.linalg.norm(R, 'fro') / np.linalg.norm(x, 'fro')
            print "relative error is", residual_error

    W = np.array(x[:, cols])

    residual_error = np.linalg.norm(R, 'fro') / np.linalg.norm(x, 'fro')
    print "Finished column picking, relative error is", residual_error

    print "Projecting all m/z bin images on the obtained basis..."
    H_full = nnls_frob(arr.reshape((arr.shape[0], -1)).T,
                       arr_pos.reshape((arr_pos.shape[0], -1))[cols, :].T)
    return W, H_full, H, R, residual_error


def do_nnmf(input_filename, output, instrument, res200, rank, args):
    """
    Parameters
    ----------
    input_filename
    output
    instrument
    res200
    rank
    args

    Returns
    -------

    """
    from rebin_dataset import do_rebinning
    arr, mz_axis = do_rebinning(input_filename, instrument, res200)
    print arr.shape
    print "Computing bin intensities... (takes a while)"
    image_intensities = arr.sum(axis=(1, 2)).compute()
    N_bright = 500
    bright_images_pos = image_intensities.argsort()[::-1][:N_bright]
    mz_axis_pos = np.array(mz_axis)[bright_images_pos]
    print 'Computing nnmf'
    W, H_full, H, R, residual_error = nnmf_calculation(arr, bright_images_pos, rank)
    print "Computing noise statistics..."
    noise_stats = {'prob': [], 'sqrt_median': [], 'sqrt_std': []}
    percent_complete = 5.0

    imzb = ImzbReader(input_filename)
    min_intensities = np.zeros((imzb.height, imzb.width))
    min_intensities[:] = np.inf

    for i, (mz, ppm) in enumerate(mz_axis):
        orig_img = imzb.get_mz_image(mz, ppm)
        orig_img[orig_img < 0] = 0
        approx_img = W.dot(H_full[:, i]).reshape((imzb.height, imzb.width))
        diff = orig_img - approx_img
        noise = diff[diff > 0]

        mask = orig_img > 0
        min_intensities[mask] = np.minimum(min_intensities[mask], orig_img[mask])

        noise_prob = float(len(noise)) / (imzb.width * imzb.height)

        noise_stats['prob'].append(noise_prob)

        if noise_prob > 0:
            noise = np.sqrt(noise)
            noise_stats['sqrt_median'].append(np.median(noise))
            noise_stats['sqrt_std'].append(np.std(noise))
        else:
            noise_stats['sqrt_median'].append(0)
            noise_stats['sqrt_std'].append(0)
        if float(i + 1) / len(mz_axis) * 100.0 > percent_complete:
            print "{}% done".format(percent_complete)
            percent_complete += 5
    print "100% done"

    with open(output, "w+") as f:
        np.savez_compressed(f, W=W, H=H_full, mz_axis=mz_axis, shape=(imzb.height, imzb.width),
                            noise_prob=np.array(noise_stats['prob']),
                            noise_sqrt_avg=np.array(noise_stats['sqrt_median']),
                            noise_sqrt_std=np.array(noise_stats['sqrt_std']),
                            min_intensities=min_intensities)
        print "Saved NMF and noise stats to {} (use numpy.load to read it)".format(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="compute NMF of a centroided dataset")
    parser.add_argument('input_file_imzb', type=str, help="input file in .imzb format")
    parser.add_argument('output_file_np', type=str, help="output file (numpy-readable NMF)")
    parser.add_argument('--instrument', type=str, default='orbitrap', choices=['orbitrap', 'fticr'])
    parser.add_argument('--res200', type=float, default=140000)
    parser.add_argument('--rank', type=int, default=40, help="desired factorization rank")
    args = parser.parse_args()
    if args.rank < 10:
        sys.stdout.write("Factorization rank must be at least 10! Exiting.\n")
        sys.exit(1)
    do_nnmf(args.input_file_imzb, args.output_file_np, args.instrument, args.res200, args.rank, args)
