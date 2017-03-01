#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
from argparse import RawTextHelpFormatter
import glob


"""
Arguments
"""
parser = argparse.ArgumentParser(
description='''
This script lets you search for all MaxResid and RMSE inside a directory.
''',
formatter_class=RawTextHelpFormatter
)

parser.add_argument(
'--metric',
type=str,
default=None,
help=
'''
Metric to be grepped.
'''
)

parser.add_argument(
'--get',
type=str,
default=None,
help=
'''
Get the metric for the quantity that you want:

Example:
    python grep.py --get forces --metric MaxResid
    python grep.py --get energy --metric rmse
'''
)

parser.add_argument(
'--directory',
type=str,
default=None,
help=
'''
Path to directory to search -log.txt files.
'''
)

args = parser.parse_args()

if args.directory == None:
    args.directory = '.'

listing = glob.glob(args.directory+'/*.txt')
print(listing)

if args.get.lower() == 'forces':
    if args.metric.lower() == 'maxresid':
        arrindex = -2
    else:
        arrindex = -4

elif args.get.lower() == 'energy':
    if args.metric.lower() == 'maxresid':
        arrindex = 5
    else:
        arrindex = 3


metrics = []
tofind = []
for element in listing:
    opening = open(element)
    f = opening.readlines()

    for index, line in enumerate(f):
        if '..optimization successful' in line:
            print(type(f[index - 1]))
            x = map(str, f[index - 1].split())
            add = [element, x[arrindex]]
            metrics.append(add)
            tofind.append(add[1])
            opening.close()
minimum = min(tofind)
maximum = max(tofind)

for _ in metrics:
    print(_)
    if minimum in _:
        print('Minimum error')
        print(_)
        break

for _ in metrics:
    if maximum in _:
        print('Maximum error')
        print(_)
        break

