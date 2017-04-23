"""Provides pre-processing functionality.

Abstracts paths and filenames so we don't have to think about them. Currently,
in use by Brandon, but will extend to general users in the future.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import re
import pdb
import json
import logging
import tempfile
from pprint import pprint
from subprocess import Popen, PIPE

import pandas as pd
import numpy as np
from pympler.asizeof import asizeof          # for profiling memory usage

# Absolute path to this file.
_WORD_SPLIT = re.compile(r'([.,!?\"\':;)(])|\s')
HERE = os.path.dirname(os.path.realpath(__file__))
DATA_ROOTS = {
    'brandon': '/home/brandon/Datasets/reddit',
    'ivan': '/Users/ivan/Documents/sp_17/reddit_data',
    'mitch': '/Users/Mitchell/Documents/Chatbot/RedditData',
    'george': '/Users/George/Documents/ChatbotData/reddit'
}
# Maximum memory usage allowed (in GiB).
MAX_MEM = 2.0


def prompt(text, default="", required=False):
    print("%s (default=%r): " % (text, default), end="")
    errors = 0
    userinput = input()
    while not userinput and required:
        errors += 1
        userinput = input("C'mon dude, be serious%s " % (
            ':' if errors <= 1 else ('!' * errors)))
    return userinput or default


class DataHelper:
    """Manages file locations and computing resource during preprocessing.

    This interacts directly with the user and double-checks their work; It makes it
    harder for you to screw up.
    """

    def __init__(self, log_level=logging.WARNING):
        """ Establish some baseline data with the user.
        """
        self.logfile = tempfile.NamedTemporaryFile(mode='w', delete=False)
        self.logfile.close()
        logging.basicConfig(filename=self.logfile.name, level=log_level)
        print("Using logfile:", self.logfile.name)

        self.file_counter = 0   # current file we're processing
        self._word_freq = None  # temporary: for parallelizing frequency dict

        print("Hi, I'm a DataHelper. For now, I help with the reddit dataset.")
        print("At any prompt, press ENTER if you want the default value.")

        # 1. Get user name. We can associate info with a given user as we go.
        user = prompt("Username", default="brandon").lower()
        if user not in DATA_ROOTS:
            print("I don't recognize you, %s." % user)
            self.data_root = prompt("Please give me the path to your data:",
                                    required=True)
        else:
            self.data_root = DATA_ROOTS[user]

        print("Hello, %s, I've set your data root to %s" % (user, self.data_root))

        # 2. Get absolute paths to all data filenames in self.file_paths.
        self.file_paths = []
        years = prompt("Years to process", default="2007,2008,2009").split(',')
        for y in years:
            # The path is: $ROOT/raw_data/$YEAR
            # Add the entirety of the directory to the file paths.
            base_path = os.path.join(self.data_root, 'raw_data', y)
            rel_paths = os.listdir(base_path)
            self.file_paths.extend([
                os.path.join(base_path, f) for f in rel_paths \
                if not f.endswith(".bz2")
            ])
        print("These are the files I found:")
        pprint(self.file_paths)
        print()

        _max_mem = prompt("Maximum memory to use (in GiB)", "%.2f" % MAX_MEM)
        try:
            self.max_mem = float(_max_mem)
        except ValueError:
            print("C'mon dude, get it together!")

        # Load the helper dictionaries from preprocessing_dicts.json, which contain the following:
        #   - First line:   a list of regular expressions to match against the data.
        #   - Second line:  a list of _replacements_ for the mathces in line one.
        #                   These should obviously have a 1-1 relationship.
        #   - Third line:   k -> v pairs of contractions.
        json_filename = "preprocessing_dicts.json"
        with open(os.path.join(HERE, json_filename), 'r') as file:
            json_data = [json.loads(line) for line in file]
            # TODO: more descriptive names for the 'modify_' objects here would be nice.
            self.modify_list, self.modify_value, self.contractions = json_data

        print("Loaded parameters from %s." % json_filename)

    def safe_load(self):
        """ Load data while keeping an eye on memory usage."""

        if self.file_counter >= len(self.file_paths):
            print("No more files to load!")
            return None

        # For in-place appending.
        # S.O.: https://stackoverflow.com/questions/20906474/
        list_ = []  # real descriptive :)
        for i in range(self.file_counter, len(self.file_paths)):
            # lines=True means "read as json-object-per-line."
            list_.append(pd.read_json(self.file_paths[i], lines=True))

            mem_usage = float(asizeof(list_)) / 1e9
            logging.info("Data list has size %.3f GiB" % mem_usage)
            if mem_usage > self.max_mem:
                print("Past max capacity: %r! Leaving data collection early." % mem_usage)
                break

        # If the user decides they want to continue loading later (when memory frees up),
        # we want the file_counter set so that it starts on the next file.
        self.file_counter = i + 1

        df = pd.concat(list_).reset_index()
        init_num_rows = len(df.index)
        logging.info("Number of lines in raw data file: %r" % init_num_rows)
        logging.info("Column names from raw data file: %r"  % df.columns)
        logging.info("DataHelper.safe_load: df.head() = %r" % df.head())
        return df

    def set_word_freq(self, wf):
        """Hacky (temporary) fix related to multiprocessing.Pool complaints
        for the reddit preprocessing script.
        """
        self._word_freq = wf

    @property
    def word_freq(self):
        return self._word_freq

    def generate_files(self, from_file_path, to_file_path, root_to_children, comments_dict):
        """ Generates two files, [from_file_path] and [to_file_path] of 1-1 comments."""
        from_file_path = os.path.join(self.data_root, from_file_path)
        to_file_path = os.path.join(self.data_root, to_file_path)

        with open(from_file_path, 'w') as from_file:
            with open(to_file_path, 'w') as to_file:
                for root_ID, child_IDs in root_to_children.items():
                    for child_ID in child_IDs:
                        try:
                            from_file.write(comments_dict[root_ID].strip() + '\n')
                            to_file.write(comments_dict[child_ID].strip() + '\n')
                        except KeyError:
                            pass

        (num_samples, stderr) = Popen(
            ['wc', '-l', from_file_path], stdout=PIPE).communicate()
        num_samples = int(num_samples.strip().split()[0])

        print("Final processed file has %d samples total." % num_samples)

        # First make sure user has copy of bash script we're about to use.
        os.popen('cp %s %s' % (os.path.join(HERE, 'split_into_n.sh'), self.data_root))

        # Split data into 90% training and 10% validation.
        os.popen('bash %s %d' % (os.path.join(self.data_root, 'split_into_n.sh'),
                                 0.1 * num_samples))

    def df_generator(self):
        """ Generates df from single files at a time."""
        for i in range(len(self.file_paths)):
            df = pd.read_json(self.file_paths[i], lines=True)
            init_num_rows = len(df.index)
            logging.info("Number of lines in raw data file: %r" % init_num_rows)
            logging.info("Column names from raw data file: %r"  % df.columns)
            yield df

    @staticmethod
    def random_rows_generator(num_rows_per_print, num_rows_total):
        """ Fun generator for viewing random comments (rows) in dataframes."""
        num_iterations = num_rows_total // num_rows_per_print
        shuffled_indices = np.arange(num_rows_per_print * num_iterations)
        np.random.shuffle(shuffled_indices)
        for batch in shuffled_indices.reshape(num_iterations, num_rows_per_print):
            yield batch

    @staticmethod
    def word_tokenizer(sentences):
        """ Tokenizes sentence / list of sentences into word tokens."""
        # Minor optimization: pre-create the list and fill it.
        tokenized = [None for _ in range(len(sentences))]
        for i in range(len(sentences)):
            tokenized[i] = [
                w for w in _WORD_SPLIT.split(sentences[i].strip()) if w
            ]

        return tokenized

    @staticmethod
    def df_to_json(df, target_file=None):
        """Converts dataframe to json object in the intuitive way, i.e.
        each row is converted to a json object, where columns are properties. If
        target_file is not None, then each such object is saved as a line in the
        target_file. Helpful because pandas default args are NOT this behavior.
        
        Args:
            df: Pandas DataFrame.
            target_file: Where to save the json-converted df. 
                If None, just return the json object.
        """

        if target_file is None:
            return df.to_json(orient='records', lines=True)
        df.to_json(path_or_buf=target_file, orient='records', lines=True)
