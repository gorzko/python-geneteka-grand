#!/usr/bin/python3

"""
Merges raw data from geneteka into larger json files.

Converts every raw row (a JSON array with HTML snippets) into a dict
with named fields. The last "Uwagi" column is fully parsed into:

- comments      - [i]-icon tooltips from any column (split by \r,
                  i.e. the encoded &#013; entity)
- archives      - [z]-icon tooltip ("Miejsce przechowywania ksiąg")
- archives_url  - href of the <a> wrapping z.png (absent if no link)
- scan_url      - href of the <a> wrapping s.png (absent if no scan)
- user_entered  - uname from the [a]-icon link ("Indeks dodał")

No raw HTML is kept in the output; data_raw keeps the raw API
responses (1:1) so this step can always be re-run.
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


def asText(value):
  """Coerces any cell value (the API may return ints, e.g. the year) to str."""
  if isinstance(value, str):
    return value
  return '' if value is None else str(value)


def unescape(value):
  return html.unescape(value).strip()


def imgField(tag, field):
  match = re.search(field + r'="([^"]*)"', tag)
  return match.group(1) if match else ''


def extractComments(cell):
  """Extracts comments from [i]-icon tooltips in any table cell.

  A single tooltip may contain multiple comments separated by \r
  (encoded as &#013; in the HTML, decoded by html.unescape).
  """
  comments = []
  for tag in IMG_TAG_RE.findall(asText(cell)):
    if os.path.basename(imgField(tag, 'src')) == 'i.png':
      title = unescape(imgField(tag, 'title'))
      comments.extend(
          part.strip() for part in title.split('\r') if part.strip())
  return comments


def extractLinks(cell):
  """Returns [(href, inner_html), ...] for all links in a cell."""
  return [
      (html.unescape(href), inner)
      for href, inner in LINK_RE.findall(asText(cell))]


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
  """Parses the "Uwagi" column into a dict with all available extras.

  Keys are simply omitted when the corresponding information is absent
  (e.g. an archive without a website, or a record without a scan).
  """
  output = {}
  archiveUrls = extractUrlsAround(stuff, r'z\.png')
  if archiveUrls:
    output['archives_url'] = archiveUrls[0]
  scanUrls = extractUrlsAround(stuff, r's\.png')
  if scanUrls:
    # A record has at most one scan link.
    output['scan_url'] = scanUrls[0]
  match = re.search(r'uname=([^"&]*)', asText(stuff))
  if match:
    output['user_entered'] = match.group(1)
  return output


def extractNotes(value):
  """Splits a cell into (text, note) using the [i] icon tooltip."""
  value = asText(value)
  match = re.search(r'i\.png"[^>]*title="([^"]*)"', value)
  if match:
    return (value.split('<', 1)[0].strip(), unescape(match.group(1)))
  return (value.strip(), None)


def convertPersonRecord(record):
  """Converts a raw birth/death row into a structured dict."""
  def col(index):
    return asText(record[index]) if index < len(record) else ''

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
  }

  # Notes attached to surname cells.
  if lastNameNotes:
    output['last_name_notes'] = lastNameNotes
  if motherLastNameNotes:
    output['mother_last_name_notes'] = motherLastNameNotes

  # Comments from every column, deduplicated, in order of appearance.
  allComments = []
  for cell in record:
    allComments.extend(extractComments(cell))
  if allComments:
    seen = set()
    output['comments'] = [
        c for c in allComments if not (c in seen or seen.add(c))]

  # Fully parsed "Uwagi" column (archive, archive URL, scan URL, user).
  output.update(extractStuff(stuff))
  return output


def convertMarriageRecord(record):
  """Converts a raw marriage row into a structured dict."""
  def col(index):
    return asText(record[index]) if index < len(record) else ''

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
  }

  if husbandLastNameNotes:
    output['nazwisko_meza_uwagi'] = husbandLastNameNotes
  if wifeLastNameNotes:
    output['nazwisko_zony_uwagi'] = wifeLastNameNotes

  # Comments from every column, deduplicated, in order of appearance.
  allComments = []
  for cell in record:
    allComments.extend(extractComments(cell))
  if allComments:
    seen = set()
    output['comments'] = [
        c for c in allComments if not (c in seen or seen.add(c))]

  # Fully parsed "Uwagi" column.
  output.update(extractStuff(stuff))
  return output


def main():
  # Map from prefix to list of records.
  data = defaultdict(list)

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
