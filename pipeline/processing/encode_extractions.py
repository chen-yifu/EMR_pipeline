"""
Encodes the extractions to numbers in code book.
"""

from typing import Dict, List, Union, Tuple

import pandas as pd
import spacy
from spacy.tokens import Span

from pipeline.processing.specific_functions import *
from pipeline.utils.column import Column, table
from pipeline.utils.encoding import Encoding
from pipeline.utils.report import Report
from pipeline.utils.value import Value
import os

nlp = spacy.load("en_core_sci_lg")


def clean_txt(val: str) -> str:
    if type(val) is float or pd.isna(val) or val is None:
        return ""
    is_num = any([True for l in val if l.isnumeric()])
    if is_num:
        return val
    else:
        no_punc = val.translate(table).strip()
        remove_letters = " ".join([w for w in no_punc.split() if len(w) > 1])
        return remove_letters


def contains_word(encoding_val: str, pipeline_val_str: str, alpha: float, threshold: float) -> bool:
    if encoding_val in pipeline_val_str:
        if encoding_val == pipeline_val_str:
            return True
        if alpha < threshold:
            start_i = pipeline_val_str.find(encoding_val)
            end_i = start_i + len(encoding_val) - 1
            if start_i != 0 and end_i != len(pipeline_val_str) - 1:
                if not pipeline_val_str[start_i - 1].isalpha() and not pipeline_val_str[end_i + 1].isalpha():
                    return True
            else:
                if start_i == 0:
                    if pipeline_val_str[end_i + 1] == " ":
                        return True
                if end_i == len(pipeline_val_str) - 1:
                    if pipeline_val_str[start_i - 1] == " ":
                        return True
    return False


def encode_extractions(reports: List[Report], code_book: Dict[str, List[Encoding]], input_threshold: float,
                       columns: Dict[str, Column], filter_values: bool, tools: dict = {}, model: str = "en_core_sci_lg",
                       training: bool = False, print_debug: bool = True, ) -> List[Report]:
    """
    :param filter_values:
    :param input_threshold:
    :param training:
    :param columns:
    :param print_debug:
    :param reports:
    :param code_book:
    :param tools:
    :param model:
    :return:
    """
    print("Beginning to encode the extractions using {}".format(model))

    def encode_extraction_for_single_report(extractions: Dict[str, Value]) -> Dict[str, str]:
        """
        :param extractions:
        :return:
        """
        encoded_extractions_dict = {}
        for human_col, encodings in code_book.items():
            done_encoding = False
            column_info = columns[human_col] if human_col in columns else None
            col_threshold = column_info.spacy_threshold if human_col in columns else .75
            remove_stop_words = column_info.remove_stopwords if human_col in columns else False

            def is_val_medical(val_to_encode: List[Span], least_neg: float = .65) -> bool:
                """
                :param val_to_encode:
                :param least_neg:
                :return:
                """
                negations = ["not applicable", "n/a", "na", "no"]
                for negation in negations:
                    n_doc = nlp(negation)
                    for pipeline_val in val_to_encode:
                        pipeline_val_str = pipeline_val.text if isinstance(pipeline_val, Span) else pipeline_val
                        pipeline_doc = nlp(pipeline_val_str)
                        sim = pipeline_doc.similarity(n_doc)
                        if sim > least_neg:
                            return False
                return True

            def try_encoding_scispacy(str_to_encode: str) -> Tuple[bool, str, int]:
                """
                :param str_to_encode:
                :return:
                """
                if not str_to_encode:
                    str_to_encode = ""
                num = ""
                found = False
                pipeline_val_str_to_return = ""
                threshold = input_threshold if training else col_threshold
                total = 1
                # init this to very low number
                alpha = float("-inf")
                for encoding in encodings:
                    for encoding_val in encoding.val:
                        code_book_doc = nlp(encoding_val)
                        pipeline_doc = nlp(str_to_encode)
                        sim = pipeline_doc.similarity(code_book_doc)
                        if sim > alpha and sim > threshold:
                            alpha = sim
                            num = str(encoding.num)
                            found = True
                            pipeline_val_str_to_return = str_to_encode
                        if alpha == 1 or contains_word(encoding_val.lower().strip(), str_to_encode.lower().strip(),
                                                       alpha, threshold):
                            # and sim > threshold
                            return True, str(encoding.num), 1
                if found:
                    return found, num, alpha
                else:
                    return found, pipeline_val_str_to_return, alpha

            for encoding in encodings:
                # is the encoding is -1 it means it either depends on another column or uses a special function to be
                # encoded
                if encoding.num == -1:
                    possible_function_name = encoding.val[0].strip().lower()
                    human_column_name = human_col.lower().strip()
                    possible_functions = [f.lower().strip() for f in tools.keys()]
                    if human_column_name in possible_functions:
                        func_in_tools = tools[human_column_name]
                    elif possible_function_name in possible_functions:
                        func_in_tools = tools[possible_function_name]
                    else:
                        print("Function for {} not found, will use default function".format(
                            human_column_name + " | " + possible_function_name))
                        func_in_tools = do_nothing
                    try:
                        val = extractions[human_col].primary_value
                        encoded_extractions_dict[human_col] = func_in_tools(val, encoded_extractions_dict)
                    except Exception as e:
                        try:
                            encoded_extractions_dict[human_col] = func_in_tools(encoded_extractions_dict)
                        except Exception as e:
                            print(e)
                            encoded_extractions_dict[human_col] = "pipeline malfunction"
                    done_encoding = True

            # try to find the highest number, if its one then we return that num
            if not done_encoding:
                try:
                    primary_val = extractions[human_col].primary_value
                    alt_val = extractions[human_col].alternative_value[0] if extractions[
                                                                                 human_col].alternative_value != [] else ""
                    found_primary, primary_encoded_value, primary_alpha = try_encoding_scispacy(primary_val)
                    found_alt, alt_encoded_value, alt_alpha = try_encoding_scispacy(alt_val)
                    if primary_alpha == 1 and found_primary:
                        encoded_extractions_dict[human_col] = primary_encoded_value
                    elif alt_alpha == 1 and found_alt:
                        encoded_extractions_dict[human_col] = alt_encoded_value
                    elif primary_alpha > alt_alpha and found_primary:
                        encoded_extractions_dict[human_col] = primary_encoded_value
                    elif alt_alpha > primary_alpha and found_alt:
                        encoded_extractions_dict[human_col] = alt_encoded_value
                    else:
                        should_return_val = is_val_medical(primary_val) if filter_values else True
                        encoded_extractions_dict[human_col] = primary_val if should_return_val else ""
                except KeyError:
                    print("This should of not occurred.")

        return encoded_extractions_dict

    for index, report in enumerate(reports):
        q25 = int(len(reports) / 4)
        q50 = q25 * 2
        q75 = q25 * 3
        if print_debug:
            if index == q25:
                print("Done encoding 25% of the reports. Current report: {}".format(report.report_id))
            elif index == q50:
                print("Done encoding 50% of the reports. Current report: {}".format(report.report_id))
            elif index == q75:
                print("Done encoding 75% of the reports. Current report: {}".format(report.report_id))
        report.encoded = encode_extraction_for_single_report(report.extractions)
    return reports
