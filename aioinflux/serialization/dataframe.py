import re
from collections import defaultdict
from functools import reduce
from itertools import chain
from typing import Union, Dict

import pandas as pd
import numpy as np

from .common import *


DataFrameType = Union[bool, pd.DataFrame, Dict[str, pd.DataFrame]]


def make(resp) -> DataFrameType:
    """Makes a dictionary of DataFrames from a response object"""

    def maker(series) -> pd.DataFrame:
        df = pd.DataFrame(series.get('values', []), columns=series['columns'])
        if 'time' not in df.columns:
            return df
        df: pd.DataFrame = df.set_index(pd.to_datetime(df['time'])).drop('time', axis=1)
        df.index = df.index.tz_localize('UTC')
        df.index.name = None
        if 'tags' in series:
            for k, v in series['tags'].items():
                df[k] = v
        if 'name' in series:
            df.name = series['name']
        return df

    def get_name(series):
        tags = [f'{k}={v}' for k, v in series.get('tags', {}).items()]
        return ','.join(filter(None, [series.get('name'), *tags])) or None

    def drop_zero_index(df):
        if isinstance(df.index, pd.DatetimeIndex):
            if all(i.value == 0 for i in df.index):
                df.reset_index(drop=True, inplace=True)

    dfs = defaultdict(list)
    for statement in resp['results']:
        for series in statement.get('series', []):
            dfs[get_name(series)].append(maker(series))
    dfs = {k: pd.concat(v, axis=0) for k, v in dfs.items()}

    # Post-processing
    for df in dfs.values():
        drop_zero_index(df)

    if len(dfs) == 1:
        return list(dfs.values())[0]
    return dfs


def parse(df, measurement, tag_columns=None, **extra_tags):
    """Converts a Pandas DataFrame into line protocol format"""

    def _itertuples(df):
        """Custom implementation of ``DataFrame.itertuples`` that
        returns plain tuples instead of namedtuples. About 50% faster.
        """
        cols = [df.iloc[:, k] for k in range(len(df.columns))]
        return zip(df.index, *cols)

    def _replace(df):
        obj_cols = {k for k, v in dict(df.dtypes).items() if v is np.dtype('O')}
        other_cols = set(df.columns) - obj_cols
        obj_nans = (f'{k}="nan"' for k in obj_cols)
        other_nans = (f'{k}=nan' for k in other_cols)
        replacements = [
            ('|'.join(chain(obj_nans, other_nans)), ''),
            (',{2,}', ','),
            ('|'.join([', ,', ', ', ' ,']), ' '),
        ]
        return replacements

    # Pre-processing
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError('DataFrame index is not DatetimeIndex')
    tag_columns = set(tag_columns or [])
    isnull = df.isnull().any(axis=1)

    # Make parser function
    tags = []
    fields = []
    for k, v in extra_tags.items():
        tags.append(f"{k}={escape(v, key_escape)}")
    for i, (k, v) in enumerate(df.dtypes.items()):
        k = k.translate(key_escape)
        if k in tag_columns:
            tags.append(f"{k}={{p[{i+1}]}}")
        elif issubclass(v.type, np.integer):
            fields.append(f"{k}={{p[{i+1}]}}i")
        elif issubclass(v.type, (np.float, np.bool_)):
            fields.append(f"{k}={{p[{i+1}]}}")
        else:
            # String escaping is skipped for performance reasons
            # Strings containing double-quotes can cause strange write errors
            # and should be sanitized by the user.
            # e.g., df[k] = df[k].astype('str').str.translate(str_escape)
            fields.append(f"{k}=\"{{p[{i+1}]}}\"")
    fmt = (f'{measurement}', f'{"," if tags else ""}', ','.join(tags),
           ' ', ','.join(fields), ' {p[0].value}')
    f = eval("lambda p: f'{}'".format(''.join(fmt)))

    # Map/concat
    if isnull.any():
        lp = map(f, _itertuples(df[~isnull]))
        rep = _replace(df)
        lp_nan = (reduce(lambda a, b: re.sub(*b, a), rep, f(p))
                  for p in _itertuples(df[isnull]))
        return '\n'.join(chain(lp, lp_nan)).encode('utf-8')
    else:
        return '\n'.join(map(f, _itertuples(df))).encode('utf-8')
