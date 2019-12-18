#!/usr/bin/env python3

import argparse
import json
import re
import subprocess


def get_counters():
    """Get netstat UDP counters. On Ubuntu, require package net-tools to be
    installed."""
    output = subprocess.check_output(["netstat", "-su"]).decode('utf8').splitlines()

    counters = {}
    reading = False

    for line in output:
        # beginning of Udp: section, start reading counters
        if line == 'Udp:':
            reading = True
            continue

        # new section
        if re.match(r'^.*:$', line):
            reading = False
            continue

        if not reading:
            continue

        data = line.split()
        value, name = (int(data[0]), ' '.join(data[1:]))
        counters[name] = value

    return counters


def display_counters(counters):
    for name, value in sorted(counters.items()):
        print('\t%s: %s' % (name, value))


def display_counters_diff(old, new):
    for name, value in old.items():
        if new[name] != value:
            print(
                '%s: %s/%s (%+d)' % (
                    name,
                    value,
                    new[name],
                    int(new[name]) - int(value)
                )
            )



def main():
    parser = argparse.ArgumentParser()
    # No args, but at least we have -h
    parser.parse_args()

    orig_counters = get_counters()
    print('netstat counters:')
    display_counters(orig_counters)

    input('Run some traffic and press enter...')
    new_counters = get_counters()
    print('netstat counters:')
    display_counters(new_counters)

    print('=== differences ===')
    display_counters_diff(orig_counters, new_counters)

if __name__ == '__main__':
    main()
