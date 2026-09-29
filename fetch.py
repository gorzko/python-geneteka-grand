#!/usr/bin/python3

"""
Fetches data from the Geneteka database (https://geneteka.genealodzy.pl).

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
  search_lastname2 - second surname (mother's for B/D, spouse for S)
  search_name2    - second given name(s)
  from_date       - year range start
  to_date         - year range end
  exac=1          - exact match (no diacritics/phonetic fuzzy matching)
  pair=1          - treat name and name2 as a pair (spouses/child+mother)
  parents=1       - also search by parents' names
  near=1          - also search in nearby parishes

API quirk: getAct.php only returns complete, correctly paginated pages of
the right record type when a name/surname filter is active. Without such a
filter the server truncates pages to a handful of rows, ignores pagination
and may mix record types, and the parents columns are empty. Whole-parish
downloads therefore work in two passes: pass 1 lists the records of the
unfiltered query, pass 2 fetches them per surname with search_lastname
(complete pages, correct type, parents included). Because search_lastname
matches the surname of both spouses, one surname query covers every
record that contains it, so pass 2 also skips already covered surnames.

The raw JSON responses of pass 2 are saved unmodified, so every column
the API returns (including the "stuff" column with [i] tooltips, archive
info and scan links) is preserved for merge.py to parse.
"""

import argparse
import hashlib
import math
import os
import re
import sys
import time
import urllib.parse

import requests

BASE_URL = 'https://geneteka.genealodzy.pl'
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
      # The server sometimes answers 200 with an empty (non-JSON) body,
      # e.g. when throttling; treat that as a failed attempt and retry.
      response.json()
      return response
    except (requests.RequestException, ValueError) as e:
      lastError = e
      if attempt + 1 < MAX_RETRIES:
        print('Warning: request failed ({}); retrying in {} s...'.format(
            e, 5 * (attempt + 1)))
        time.sleep(5 * (attempt + 1))
  raise lastError


def stripCellHtml(value):
  """Returns the cell text with any icon/tooltip HTML removed."""
  text = str(value)
  text = re.sub(r'<img\b[^>]*>', '', text)
  text = re.sub(r'<[^>]+>', '', text)
  return text.strip()


def recordKey(row):
  """Identity of a raw record: year, number, names and surnames."""
  return tuple(str(row[i]).strip() if i < len(row) else '' for i in range(8))


def recordKeyNoParents(row):
  """Identity of a record, ignoring the parents columns (4 and 7).

  Pass-1 (unfiltered) rows have the parents columns empty while pass-2
  rows have them filled, so recordKey cannot match a record across the
  two passes; this key can.
  """
  return tuple(
      str(row[i]).strip() if i < len(row) else '' for i in (0, 1, 2, 3, 5, 6))


def fetchPaged(session, filters, prefix, pageCounter, maxTotal=None):
  """Fetches all pages of one filtered query, saving each response 1:1.

  pageCounter is a one-element list holding the next data_raw file number,
  shared across per-surname queries. maxTotal, when given, is recordsTotal
  of the same query without the surname filter: a filtered result can never
  be bigger, so a page reporting more records than maxTotal is a server
  glitch (the API intermittently ignores rid/search and answers for the
  whole region). Glitched pages are retried and the surname is skipped if
  the glitch persists. Returns the merged rows.
  """
  result = None
  start = 0
  totalPages = None
  emptyWarned = False
  glitchRetries = 0
  while True:
    print('Fetching {} page {}/{}'.format(
        os.path.basename(prefix), start // PAGE_SIZE + 1,
        totalPages if totalPages else '?'))
    response = fetchPage(session, filters, start, PAGE_SIZE)
    data = response.json()
    rows = data.get('data', [])
    total = int(data.get('recordsTotal', 0))
    if result is None:
      if maxTotal is not None and total > maxTotal:
        # Server glitch: a filtered query cannot return more records
        # than the same query without the filter. Retry before skipping.
        if glitchRetries < MAX_RETRIES:
          glitchRetries += 1
          print('Warning: API reported {} records (> {} without the '
                'surname filter); server glitch, retrying...'.format(
                    total, maxTotal))
          time.sleep(5 * glitchRetries)
          continue
        print('Warning: API keeps reporting {} records (> {}); skipping '
              'this surname - re-run fetch.py to complete it.'.format(
                  total, maxTotal))
        return []
      if not rows and total > 0 and not emptyWarned:
        # The API sometimes returns an empty page together with
        # recordsTotal > 0 (server-side glitch). Retry the page before
        # giving up, so we do not silently save an empty data_raw file.
        emptyWarned = True
        print('Warning: API reported {} records but returned an empty '
              'page; retrying...'.format(total))
        time.sleep(5)
        continue
      result = data
      totalPages = max(1, int(math.ceil(1.0 * total / PAGE_SIZE)))
      if total == 0:
        print('No records found.')
        return []
    else:
      if not rows:
        print('Warning: page at start={} returned no rows.'.format(start))
      result['data'].extend(rows)
    fileName = '{}_{:05d}.json'.format(prefix, pageCounter[0])
    with open(fileName, 'w') as f:
      f.write(response.text)
    pageCounter[0] += 1
    start += PAGE_SIZE
    if start >= totalPages * PAGE_SIZE:
      break
    # Sleep not to overload the server with continuous load.
    time.sleep(SLEEP_SECONDS)
  return result['data']


def enumerateRows(session, filters):
  """Pass 1: fetches the unfiltered query to list all its records.

  Unfiltered responses may be truncated and may mix record types, so they
  are only used to plan pass 2 and are NOT saved to data_raw.
  Returns (rows, recordsTotal of the unfiltered query).
  """
  rows = []
  start = 0
  totalPages = None
  total = 0
  while True:
    print('Enumerating page {}/{}'.format(
        start // PAGE_SIZE + 1, totalPages if totalPages else '?'))
    response = fetchPage(session, filters, start, PAGE_SIZE)
    data = response.json()
    pageRows = data.get('data', [])
    if totalPages is None:
      total = int(data.get('recordsTotal', 0))
      totalPages = max(1, int(math.ceil(1.0 * total / PAGE_SIZE)))
      if total == 0:
        print('No records found.')
        break
    rows.extend(pageRows)
    start += PAGE_SIZE
    if start >= totalPages * PAGE_SIZE:
      break
    time.sleep(SLEEP_SECONDS)
  return rows, total


def fetchAll(filters, outputDir):
  prefix = outputPrefix(filters, outputDir)
  if not os.path.exists(outputDir):
    os.makedirs(outputDir)
  session = requests.Session()
  # Warm up the session so we get any cookies the API expects.
  session.get(INDEX_URL, params={k: v for k, v in filters.items()},
              timeout=30, headers={'User-Agent': 'python-geneteka/2.0'})

  # Without a name/surname filter the API truncates pages, ignores
  # pagination and returns no parents, so whole-parish downloads are
  # fetched per surname (pass 1 lists the records, pass 2 fetches them).
  if not (filters.get('search_lastname') or filters.get('search_name')):
    print('No name/surname filter: enumerating records (pass 1)...')
    pass1Rows, unfilteredTotal = enumerateRows(session, filters)
    # search_lastname matches the surname on either side of a record, so
    # one surname query covers every record that contain it. Index the
    # surnames of both sides and skip a surname once all its records are
    # fetched - one query for a frequent surname covers the rarer
    # surnames married into it.
    surnameToKeys = {}
    pass1Keys = set()
    for row in pass1Rows:
      key = recordKeyNoParents(row)
      pass1Keys.add(key)
      for index in (3, 6):
        if index >= len(row):
          continue
        surname = stripCellHtml(row[index])
        if surname:
          surnameToKeys.setdefault(surname, set()).add(key)
    print('Found {} records with {} unique surnames; fetching per surname '
          '(pass 2)...'.format(len(pass1Keys), len(surnameToKeys)))
    # Most frequent surnames first: they cover the most records per query.
    orderedSurnames = sorted(
        surnameToKeys.items(), key=lambda item: (-len(item[1]), item[0]))
    pageCounter = [0]
    allRows = []
    coveredKeys = set()
    seenKeys = set()
    skipCount = 0
    for surname, keys in orderedSurnames:
      if keys <= coveredKeys:
        skipCount += 1
        continue
      subFilters = dict(filters)
      subFilters['search_lastname'] = surname
      rows = fetchPaged(session, subFilters, prefix, pageCounter,
                        maxTotal=unfilteredTotal)
      for row in rows:
        coveredKeys.add(recordKeyNoParents(row))
        key = recordKey(row)
        if key in seenKeys:
          continue
        seenKeys.add(key)
        allRows.append(row)
      time.sleep(SLEEP_SECONDS)
    print('Skipped {} surname queries whose records were already fetched.'
          .format(skipCount))
    uncovered = pass1Keys - coveredKeys
    if uncovered:
      print('Warning: {} of {} records were not returned by any surname '
            'query (e.g. records with no surname on either side); '
            're-running fetch.py may fetch them.'.format(
                len(uncovered), len(pass1Keys)))
    return allRows

  pageCounter = [0]
  return fetchPaged(session, filters, prefix, pageCounter)


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
