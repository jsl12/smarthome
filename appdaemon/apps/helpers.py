import json
from datetime import datetime
from pathlib import Path
from typing import List

import pandas as pd
import yaml

BASE_DIR = Path(r'H:\smarthome\homeassistant')

HOMEASSISTANT_CONFIG_DIR = Path(r'/usr/homeassistant')

def blank_df(start: datetime, end: datetime, freq: str, entities: List[str], attributes: List[str]):
    """
    Provides a standard method for generating a blank DataFrame based on the entities and their attributes used

    Parameters
    ----------
    entities : List[str]
        List of entity IDs used
    attributes : List[str]
        List of entity attributes
    freq : str
        Frequency str
        https://pandas.pydata.org/pandas-docs/stable/user_guide/timeseries.html#timeseries-offset-aliases

    Returns
    -------
    `pd.DataFrame`
    """
    return pd.DataFrame(columns=pd.MultiIndex.from_product([entities, attributes]).drop_duplicates(),
                        index=pd.date_range(start=start, end=end, freq=freq).round('S'))


def scene_df(start: datetime, end: datetime, freq: str, entities: List[str], config_dir: Path):
    return blank_df(start, end, freq,
                    entities=expanded_entities(entity_ids=entities,
                                               config_dir=config_dir),
                    attributes=[
                        'rgb_color',
                        'color_temp',
                        'brightness_pct'
                    ])


def expanded_entities(entity_ids: List[str], config_dir: Path):
    """
    Used to convert a list of entity ids that may contain scenes into a list of all light entities

    Parameters
    ----------
    entity_ids :
    config_dir :

    Returns
    -------

    """
    entity_registry = config_dir / '.storage' / 'core.entity_registry'
    with entity_registry.open('r') as file:
        eids = {e['entity_id']: e for e in json.load(file)['data']['entities']}

    scene_user_config = config_dir / 'scenes.yaml'
    with scene_user_config.open('r') as file:
        scene_configs = yaml.load(file, Loader=yaml.SafeLoader)

    res = []
    for entity_id in entity_ids:
        if entity_id in eids:
            entity_type, entity_name = entity_id.split('.')
            if entity_type == 'light':
                res.append(entity_id)
            elif entity_type == 'scene':
                for scene in scene_configs:
                    if scene['id'] == eids[entity_id]['unique_id']:
                        res.extend(scene['entities'].keys())
        else:
            KeyError(f'{entity_id} not in {entity_registry}')
    return res

def interpolate(df: pd.DataFrame) -> pd.DataFrame:
    """
    Interpolates the columns that had both initial and final values

    Returns
    -------

    """
    # drops the columns that didn't have both initial and final values
    df = df.loc[:, ~(pd.isna(df.iloc[[0, -1]]).any())]

    for entity, profile in df.groupby(level=0, axis=1):
        df.loc[:, pd.IndexSlice[entity, :]] = (
            profile
                .droplevel(0, axis=1)
                .applymap(float)
                .interpolate('time', axis='index')
                .applymap(round)
                .applymap(int)
        ).values

    return df.drop_duplicates().sort_index(axis=1)
