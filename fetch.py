#!/usr/bin/python3

"""
Fetches data from the Geneteka database (http://www.geneteka.genealodzy.pl).

Supports all search filters available in the Geneteka GUI. The GUI form on
index.php?op=gt sends its whole query string unchanged to api/getAct.php
(see js/main.js: "ajax": {"url": "api/getAct.php", "data": $_GET}), so every
GUI filter is just a GET parameter:

  w               - voivodeship/region code (e.g. 07mz)
  bdm             - record type: B (births), S (marriages), D (deaths), A (all)
  rid             - parish id (or B/S/D/A meaning "all parishes of that type")
  lang            - pol / eng
  search_lastname - surname of the searched person
  search_name     - given name(s) of the searched person
  search_lastname2 - second surname (mother's surname for B/D, spouse for S)
  search_name2    - second given name(s)
  from_date       - year range start
  to_date         - year range end
  exac=1          - exact match (no diacritics/phonetic fuzzy matching)
  pair=1          - treat name and name2 as a pair (spouses/child+mother)
  parents=1       - also search by parents' names
  near=1          - also search in nearby parishes

For convenience you can paste a full GUI search URL with --url and all
filters will be extracted from it.

The raw JSON responses are saved unmodified, so every column the API
returns (including the "stuff" column with [i] tooltips, archive info and
scan links) is preserved for merge.py to parse.
"""

import argparse
import hashlib
import math
import os
import sys
import time
import urllib.parse

import requests

BASE_URL = 'http://www.geneteka.genealodzy.pl'
ACTS_URL = BASE_URL + '/api/getAct.php'
INDEX_URL = BASE_URL + '/index.php'
OUTPUT_DIR = 'data_raw'
PAGE_SIZE = 50          # max supported by the GUI (lengthMenu: 10, 25, 50)
SLEEP_SECONDS = 2       # be gentle to the server
MAX_RETRIES = 3

# GET parameters of the GUI search form that getAct.php understands.
FILTER_PARAMS = (
    'bdm', 'w', 'rid', 'lang',
    'search_lastname', 'search_name', 'search_lastname2', 'search_name2',
    'from_date', 'to_date',
    'exac', 'pair', 'parents', 'near',
)

# Internal Datatables parameters (pagination / sorting / search state).
TABLE_PARAMS = ('rpp1', 'rpp2', 'ordertable', 'searchtable', 'start', 'length')


def filtersFromUrl(url):
  """Extracts all Geneteka filter parameters from a GUI search URL."""
  query = urllib.parse.urlsplit(url).query
  params = urllib.parse.parse_qs(query, keep_blank_values=True)
  result = {}
  for key in FILTER_PARAMS:
    if key in params and params[key][0] != '':
      result[key] = params[key][0]
  return result


def parseArguments():
  parser = argparse.ArgumentParser(
      description='Fetch records from geneteka.genealodzy.pl',
      epilog=('Backward compatible: fetch.py <voivodeship_id> <record_type> '
              '<parish_id> (e.g. fetch.py 07mz B 944)'))
  parser.add_argument(
      'positional', nargs='*',
      help='voivodeship_id record_type parish_id (as in the old usage)')
  parser.add_argument(
      '--url', '-u',
      help='Geneteka GUI search URL; all filters are taken from it')
  parser.add_argument(
      '-w', '--voivodeship', help='voivodeship/region code (e.g. 07mz)')
  parser.add_argument(
      '-t', '--bdm', choices=['B', 'S', 'D', 'A'],
      help='record type: B, S, D or A (all)')
  parser.add_argument(
      '-r', '--rid',
      help='parish id, or B/S/D/A for all parishes of that type')
  parser.add_argument('--lang', default='pol', help='interface language (pol/eng)')
  parser.add_argument('--lastname', help='surname of the searched person')
  parser.add_argument('--name', help='given name(s) of the searched person')
  parser.add_argument('--lastname2',
      help='second surname (mother\'s for B/D, spouse\'s for S)')
  parser.add_argument('--name2', help='second given name(s)')
  parser.add_argument('--from-date', help='year range start (e.g. 1820)')
  parser.add_argument('--to-date', help='year range end (e.g. 1885)')
  parser.add_argument('--exac', action='store_true',
      help='exact match (disable fuzzy/diacritic-insensitive search)')
  parser.add_argument('--pair', action='store_true',
      help='search for name+name2 as a pair (spouses / child+mother)')
  parser.add_argument('--parents', action='store_true',
      help='also match by parents\' names')
  parser.add_argument('--near', action='store_true',
      help='also search in nearby parishes (needs a surname, GUI rule)')
  parser.add_argument('--length', type=int, default=PAGE_SIZE,
      help='records per request (max 50)')
  parser.add_argument('--output-dir', default=OUTPUT_DIR,
      help='directory for raw JSON files (default: data_raw)')
  return parser.parse_args()


def buildFilters(args):
  """Combines --url filters, CLI options and the old positional arguments."""
  filters = {}
  if args.url:
    filters.update(filtersFromUrl(args.url))
  if len(args.positional) == 3:
    filters['w'], filters['bdm'], filters['rid'] = args.positional
  elif args.positional:
    raise SystemExit(
        'Error: expected 0 or 3 positional arguments (voivodeship_id '
        'record_type parish_id), got: %s' % args.positional)
  overrides = {
      'w': args.voivodeship, 'bdm': args.bdm, 'rid': args.rid,
      'lang': args.lang,
      'search_lastname': args.lastname, 'search_name': args.name,
      'search_lastname2': args.lastname2, 'search_name2': args.name2,
      'from_date': args.from_date, 'to_date': args.to_date,
  }
  for key, value in overrides.items():
    if value is not None:
      filters[key] = value
  if args.exac:
    filters['exac'] = '1'
  if args.pair:
    filters['pair'] = '1'
  if args.parents:
    filters['parents'] = '1'
  if args.near:
    filters['near'] = '1'
  if 'w' not in filters:
    raise SystemExit('Error: voivodeship (-w) or --url is required.')
  filters.setdefault('bdm', 'A')
  filters.setdefault('rid', filters['bdm'])
  filters.setdefault('lang', 'pol')
  filters['op'] = 'gt'
  return filters


def outputPrefix(filters, outputDir):
  """Builds the output file prefix, including a tag for extra filters.

  The prefix stays compatible with merge.py: voivodeship_recordtype_parishid,
  optionally followed by an 8-char hash when search filters other than
  w/bdm/rid/lang are used, so different queries never get merged together.
  """
  extra = {
      key: value for key, value in filters.items()
      if key not in ('op', 'w', 'bdm', 'rid', 'lang') and value not in (None, '', '0')
  }
  base = '{}_{}_{}'.format(filters['w'], filters['bdm'], filters['rid'])
  if extra:
    serialized = urllib.parse.urlencode(sorted(extra.items()))
    tag = hashlib.md5(serialized.encode('utf-8')).hexdigest()[:8]
    base += '_' + tag
  return os.path.join(outputDir, base)


def fetchPage(session, filters, start, length):
  """Fetches one page of results from the getAct.php API."""
  params = dict(filters)
  params['start'] = start
  params['length'] = length
  referer = INDEX_URL + '?' + urllib.parse.urlencode(filters)
  headers = {
      'Referer': referer,
      'X-Requested-With': 'XMLHttpRequest',
      'Accept': 'application/json, text/javascript, */*; q=0.01',
  }
  lastError = None
  for attempt in range(MAX_RETRIES):
    try:
      response = session.get(ACTS_URL, params=params, headers=headers,
                             timeout=30)
      response.raise_for_status()
      return response
    except requests.RequestException as e:
      lastError = e
      if attempt + 1 < MAX_RETRIES:
        time.sleep(5 * (attempt + 1))
  raise lastError


def fetchAll(filters, outputDir):
  prefix = outputPrefix(filters, outputDir)
  session = requests.Session()
  # Warm up the session so we get any cookies the API expects.
  session.get(INDEX_URL, params={k: v for k, v in filters.items()},
              timeout=30, headers={'User-Agent': 'python-geneteka/2.0'})

  length = PAGE_SIZE
  page = 0
  result = None
  totalPages = None
  while True:
    print('Fetching {} page {}/{}'.format(
        os.path.basename(prefix), page + 1,
        totalPages if totalPages else '?'))
    response = fetchPage(session, filters, page * length, length)
    fileName = '{}_{:05d}.json'.format(prefix, page)
    with open(fileName, 'w') as f:
      f.write(response.text)
    data = response.json()
    if result is None:
      result = data
      total = int(data.get('recordsTotal', 0))
      totalPages = max(1, int(math.ceil(1.0 * total / length)))
      if total == 0:
        print('No records found.')
        return []
    else:
      result['data'].extend(data.get('data', []))
    page += 1
    if page >= totalPages:
      break
    # Sleep not to overload the server with continuous load.
    time.sleep(SLEEP_SECONDS)
  return result['data']


def main():
  args = parseArguments()
  filters = buildFilters(args)
  print('Filters: ' + str({
      k: v for k, v in filters.items() if k != 'op'}))
  if not os.path.exists(args.output_dir):
    os.makedirs(args.output_dir)
  data = fetchAll(filters, args.output_dir)
  print('Fetched {} records.'.format(len(data)))


if __name__ == '__main__':
  main()
