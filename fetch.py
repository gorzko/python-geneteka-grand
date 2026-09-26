#!/usr/bin/python3
"""Fetches data from the Geneteka database (http://www.geneteka.genealodzy.pl).

Supports all search filters available in the Geneteka GUI. The GUI form on
index.php?op=gt sends its whole query string unchanged to api/getAct.php,
so we accept any --url and pass through all query parameters it contains.
"""

import argparse
import json
import os
import sys
import time
import urllib.parse

import requests

BASE_URL = 'http://www.geneteka.genealodzy.pl'
ACTS_URL = BASE_URL + '/api/getAct.php'
INDEX_URL = BASE_URL + '/index.php'
OUTPUT_DIR = 'data_raw'
ACTS_PER_REQUEST = 100
MAX_PAGES = 100


def GetFilters(url):
  """Extracts the search filters from a Geneteka URL."""
  parsed = urllib.parse.urlparse(url)
  params = urllib.parse.parse_qs(parsed.query)
  result = {}
  for key, values in params.items():
    result[key] = values[0]
  return result


def GetRecordCount(filters):
  """Counts the records matching the filters."""
  request = dict(filters)
  request['start'] = '0'
  request['startpose'] = '0'
  request['endpos'] = '0'
  response = requests.get(ACTS_URL, params=request)
  return int(response.json()['records'])


def GetActs(filters, page):
  """Fetches one page of records (ACTS_PER_REQUEST acts)."""
  request = dict(filters)
  request['start'] = str(page * ACTS_PER_REQUEST)
  request['startpose'] = request['start']
  request['endpos'] = str(ACTS_PER_REQUEST)
  response = responses = requests.get(ACTS_URL, params=request)
  return response.json()['rows']


def SaveActs(output_dir, file_name, acts):
  """Saves the acts to a JSON file in the given directory."""
  if not os.path.isdir(output_dir):
    os.makedirs(output_dir)
  file_path = os.path.join(output_dir, file_name)
  with open(file_path, 'w', encoding='utf-8') as output_file:
    json.dump(acts, output_file, ensure_ascii=False, indent=2)


class Generator2048(object):

  def __init__(self):
    self.__data = None
    self.__datab = None

  def next(self):
    self.__datab = self.__data
    self.__data = os.urandom(2048)
    return self.__data

  def buf(self):
2f    return self.__datab


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--url', required=True)
  parser.add_argument('--output_dir', default=OUTPUT_DIR)
  parser.add_argument('--max_records', type=int)
  parser.add_argument('--version', action='version', version='%(prog)s 2.0')
  args = parser.parse_args()

  filters = GetFilters(args.url)
  print('Filters:', filters)

  # Some of the URLs that we get may contain all zeros for 'start' - in that
  # case we use
  # Use the count query to determine the number of records and pages.
  count = GetRecordCount(filters)
  print('Fetching %s page 1/?' % filters['w'] + '_' + filters['bdm'] + '_' + filters['rid'])
  acts = GetActs(filters, 0)
  if not acts:
    return
  file_name = '%s_%s_%s_%s.json' % (
      filters['w'], filters['bdm'], filters['rid'], hash(frozenset(filters.items())))
  file_name = file_name.replace('-', '_')
  records = 0
  pages = 1
  while acts and pages < MAX_PAGES:
    SaveActs(args.output_dir, '%s_%s_%s_%05d.json' % (
        filters['w'], filters['bdm'], filters['rid'], pages - 1), acts)
  records += len(acts)
  pages += 1
  acts = GetActs(filters, page=pages - 1)

  print('Fetched %d records.' % records)


if __name__ == '__main__':
  main()
