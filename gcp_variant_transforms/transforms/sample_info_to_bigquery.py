# Copyright 2019 Google LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Dict, Union  # pylint: disable=unused-import

from datetime import datetime

import apache_beam as beam

from gcp_variant_transforms.beam_io import vcf_header_io  # pylint: disable=unused-import
from gcp_variant_transforms.beam_io import vcf_parser
from gcp_variant_transforms.libs import bigquery_util
from gcp_variant_transforms.libs import hashing_util
from gcp_variant_transforms.libs import sample_info_table_schema_generator

SampleNameEncoding = vcf_parser.SampleNameEncoding
_DATETIME_FORMAT = "%Y-%m-%d %H:%M:00.0"


class ConvertSampleInfoToRow(beam.DoFn):
  """Extracts sample info from `VcfHeader` and converts it to a BigQuery row."""

  def __init__(self, sample_name_encoding):
    # type: (int) -> None
    self._sample_name_encoding = sample_name_encoding

  def _get_now_to_minute(self):
    return datetime.now().strftime(_DATETIME_FORMAT)

  def process(self, vcf_header):
    # type: (vcf_header_io.VcfHeader, bool) -> Dict[str, Union[int, str]]
    current_minute = self._get_now_to_minute()
    for sample in vcf_header.samples:
      if self._sample_name_encoding == SampleNameEncoding.WITH_FILE_PATH:
        sample = hashing_util.create_composite_sample_name(sample,
                                                           vcf_header.file_path)
      sample_id = hashing_util.generate_sample_id(sample)

      row = {
          sample_info_table_schema_generator.SAMPLE_ID: sample_id,
          sample_info_table_schema_generator.SAMPLE_NAME: sample,
          sample_info_table_schema_generator.FILE_PATH: vcf_header.file_path,
          sample_info_table_schema_generator.INGESTION_DATETIME: current_minute
      }
      yield row


class SampleInfoToBigQuery(beam.PTransform):
  """Writes sample info to BigQuery."""

  def __init__(self, output_table_prefix, sample_name_encoding, append=False):
    # type: (str, Dict[str, str], bool, int) -> None
    """Initializes the transform.

    Args:
      output_table_prefix: The prefix of the output BigQuery table.
      append: If true, existing records in output_table will not be
        overwritten. New records will be appended to those that already exist.
      sample_name_encoding: If SampleNameEncoding.WITHOUT_FILE_PATH is supplied,
        sample_id would only use sample_name in to get a hashed name; otherwise
        both sample_name and file_name will be used.
    """
    self._output_table = bigquery_util.compose_table_name(
        output_table_prefix, bigquery_util.SAMPLE_INFO_TABLE_SUFFIX)
    self._append = append
    self._sample_name_encoding = sample_name_encoding
    self._schema = sample_info_table_schema_generator.generate_schema()

  def expand(self, pcoll):
    return (pcoll
            | 'ConvertSampleInfoToBigQueryTableRow' >> beam.ParDo(
                ConvertSampleInfoToRow(self._sample_name_encoding))
            | 'WriteSampleInfoToBigQuery' >> beam.io.Write(beam.io.BigQuerySink(
                self._output_table,
                schema=self._schema,
                create_disposition=(
                    beam.io.BigQueryDisposition.CREATE_IF_NEEDED),
                write_disposition=(
                    beam.io.BigQueryDisposition.WRITE_APPEND
                    if self._append
                    else beam.io.BigQueryDisposition.WRITE_TRUNCATE))))
