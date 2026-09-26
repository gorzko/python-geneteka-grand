#!/usr/bin/python3

"""
Merges raw data from geneteka into larger json files.

Parses all columns of the raw table rows, including:
- all [i]-icon tooltips (comments) from any column,
- archive information (z.png tooltips and links),
- scan links (s.png icons linking to metryki.genealodzy.pl),
- the name of the user who indexed the record,
and keeps the complete raw row in "raw_columns".

Keeps the output keys of the original version (notes, archives,
archives_url, metryki_url, last_name_notes, ...) so generate.py works
unchanged, and adds new, generalized keys (comments, scan_url, scan_urls).
"""

from collections import defaultdict
import html
import json
import os
import re

INPUT_DIR = 'data_raw'
OUTPUT_DIR = 'data'

# <img ...> tag, e.g. <img src="i.png" title="...">
IMG_TAG_RE = re.compile(r'<img\b[^>]*>')
# <a href="...">...</a> link with its inner HTML.
LINK_RE = re.compile(r'<a\b[^>]*href="([^"]*)"[^>]*>(.*?)</a>', re.S)


def unescape(value):
  return html.unescape(value).strip()


def imgField(tag, field):
  match = re.search(field + r'="([^"]*)"', tag)
  return match.group(1) if match else ''


def extractIcons(cell):
  """Extracts (comments, archives, scans) from the [i]/[z]/[s] icons.

  Works on any table cell, not just the last "stuff" column. Comments from
  the [i] icon tooltips may contain multiple entries separated by \r.
  """
  comments = []
  archives = []
  scans = []
  for tag in IMG_TAG_RE.findall(cell or ''):
    base = os.path.basename(imgField(tag, 'src'))
    title = unescape(imgField(tag, 'title'))
    if base == 'i.png' and title:
      comments.extend(part.strip() for part in title.split('\r') if part.strip())
    elif base == 'z.png' and title:
      archives.append(title)
    elif base == 's.png':
      scans.append(title)
  return comments, archives, scans


def extractLinks(cell):
  """Returns [(href, inner_html), ...] for all links in a cell."""
  return [(html.unescape(href), inner) for href, inner in LINK_RE.findall(cell or ''

)]


def extractScans(cell):
  """Returns scan URLs (links around the s.png icon), from any cell."""
  urls = []
  for href, inner in extractLinks(cell):
    if re.search(r's\.png', inner):
      urls.append(href)
  return urls


def extractUrlsAround(cell, iconRe):
  """Returns hrefs of <a> tags whose inner HTML contains the given icon.

  Used to get the URL wrapped around z.png (archive website) and s.png
  (scan). Returns [] when no such link exists.
  """
  urls = []
  for href, inner in extractLinks(cell):
    if re.search(iconRe, inner):
      urls.append(href)
  return urls


def extractStuff(stuff):
  """Parses the "stuff" column into a dict with all available extras."""
  comments, archives, scans = extractIcons(stuff)
  links = extractLinks(stuff)
  output = {}
  if comments:
    output['comments'] = comments
    # Backward-compatible key used by generate.py.
    output['notes'] = comments
  if archives:
    output['archives'] = '\r'.join(archives)
  # URL of the place where the archives are kept: the <a> tag that
  # wraps the z.png icon (only present when the archive has a website).
  archiveUrls = extractUrlsAround(stuff, r'z\.png')
  if archiveUrls:
    output['archives_url'] = archiveUrls[0]
  # URL(s) of the scan: the <a> tag that wraps the s.png icon
  # (only present when a scan is available).
  scanUrls = extractScans(stuff)
  if scanUrls:
    output['scan_urls'] = scanUrls
    output['scan_url'] = scanUrls[0]
    # Backward-compatible key used by generate.py.
    output['metryki_url'] = scanUrls[0]
  # User that entered this record to the database.
  match = re.search(r'uname=([^"&]*)', stuff or '')
  if match:
    output['user_entered'] = match.group(1)
  return output


def convertPersonRecord(record):
  """Converts a raw birth/death row into a structured dict."""
  # Keep everything the API returned, unparsed.
  raw = list(record)

  def col(index):
    return record[index] if index < len(record) else ''

  stuff = col(9)
  lastName, lastNameNotes = extractNotes(col(3))
  motherLastName, motherLastNameNotes = extractNotes(col(6))

  output = {
    'year': col(0).strip(),
    'record_number': col(1).strip(),
    'first_name': col(2).strip(),
    'last_name': lastName,
    'father_first_name': col(4).strip(),
    'mother_first_name': col(5).strip(),
    'mother_last_name': motherLastName,
    'parish': col(7).strip(),
    'place': col(8).strip(),
    'stuff': stuff,
    'raw_columns': raw,
  }

  # Notes attached to surname cells.
  if lastNameNotes:
    output['last_name_notes'] 
= lastNameNotes
  if motherLastNameNotes:
    output['mother_last_name_notes'] = motherLastNameNotes

  # [i] comments from every column, not just the "stuff" co
lumn.
  allComments = []
  for cell in record:
    comments, _, _ = extractIcons(cell)
    allComments.extend(comments)
  if allComments:
    seen = set()
    deduped = [c for c in allComments
               if not (c in seen or seen.add(c))]
    output['comments'] = deduped
    output.setdefault('notes', deduped)

  output.update(extractStuff(stuff))
  return output


def convertMarriageRecord(record):
  """Converts a raw marriage row into a structured dict."""
  raw = list(record)

  def col(index):
    return record[index] if index < len(record) else ''

  stuff = col(9)
  husbandLastName, husbandLastNameNotes = extractNotes(col(3))
  wifeLastName, wifeLastNameNotes = extractNotes(col(6))

  output = {
    'year': col(0).strip(),
    'record_number': col(1).strip(),
    'husband_first_name': col(2).strip(),
    'husband_last_name': husbandLastName,
    'husband_parents': col(4).strip(),
    'wife_first_name': col(5).strip(),
    'wife_last_name': wifeLastName,
    'wife_parents': col(7).strip(),
    'parish': col(8).strip(),
    'stuff': stuff,
    'raw_columns': raw,
  }

  if husbandLastNameNotes:
    output['nazwisko_meza_uwagi'] = husbandLastNameNotes
  if wifeLastNameNotes:
    output['nazwisko_zony_uwagi'] = wifeLastNameNotes

  # [i] comments from every column.
  allComments = []
  for cell in record:
    comments, _, _ = extractIcons(cell)
    allComments.extend(comments)
  if allComments:
    seen = set()
    deduped = [c for c in allComments
               if not (c in seen or seen.add(c))]
    output['comments'] = deduped
    output.setdefault('notes', deduped)

  output.update(extractStuff(stuff))
  return output


def extractNotes(value):
  """Splits a cell into (text, note) using the [i] icon tooltip."""
  match = re.search(r'i\.png"[^>]*title="([^"]*)"', value)
  if match:
    return (valu
e.split('<', 1)[0].strip(), unescape(match.group(1)))
  return (value.strip(), None)


def main():
  # Map from prefix to list of records.
  data = defaultdict(li
st)

  # Read all files from INPUT_DIR.
  for fileName in os.listdir(INPUT_DIR):
    match = re.match(
        r'([^_]+)_(.)_([^_]+(?:_[0-9a-f]{8})?)_\d+\.json$', fileName)
    if not match:
      continue
    prefix = match.group(1) + '_' + match.group(2) + '_' + match.group(3)
    with open(os.path.join(INPUT_DIR, fileName)) as file:
      content = json.load(file)
      data[prefix] += content['data']

  if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

  # Parse records and write one parish per file.
  for key, value in data.items():
    voivodeship, recordType, parishId = key.split('_', 2)
    if recordType == 'S':
      converter = convertMarriageRecord
    else:
      converter = convertPersonRecord
    value[:] = [converter(x) for x in value]

    print("Writing %s" % key)
    metadata = {
      'voivodeship': voivodeship,
      'record_type': recordType,
      'parish_id': parishId,
    }
    outputFile = os.path.join(OUTPUT_DIR, key + '.json')
    with open(outputFile, 'w') as file:
      outputData = {
        'data': value,
        'metadata': metadata,
      }
      json.dump(outputData, file, ensure_ascii=False)


if __name__ == '__main__':
  main()
