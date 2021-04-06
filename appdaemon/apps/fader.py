from datetime import datetime
from pathlib import Path
from typing import Dict, List

import pandas as pd
import yaml
from appdaemon.plugins.hass import hassapi as hass

from colors import get_colors, YAML_PATH
from pandas_controller import PandasCtl


class RGBFader(PandasCtl):
    """
    Args:
        start:
        end:
        initial:
            entity_id:
                [color_name|rgb_color:]
                [brightness_pct:]
        final:
            entity_id:
                [color_name|rgb_color:]
                [brightness_pct:]
        [freq: 1min]
        [profile: <path>]
    """
    cols = ['red', 'green', 'blue']
    val_kwarg = 'rgb_color'

    def validate_args(self):
        super().validate_args()
        valid_keys = ['color_name', 'rgb_color', 'brightness_pct']

        for entity, config in self.args['initial'].items():
            assert ('color_name' in config or 'rgb_color' in config)
            assert(all([key in valid_keys for key in config.keys()]))

        for entity, config in self.args['final'].items():
            assert ('color_name' in config or 'rgb_color' in config)
            assert(all([key in valid_keys for key in config.keys()]))
            assert entity in self.args['initial'], f'{entity} is in final state, but not initial state'

    def initialize(self):
        self.validate_args()
        self.color_dict: Dict[str, List[int]] = get_colors(YAML_PATH)
        super().initialize()

    def generate_profile(self):
        self.profile = self.blank_df(entities=self.args['initial'].keys(), attributes=self.cols)
        self.place_vals(self.profile.index[0], 'initial')
        self.place_vals(self.profile.index[-1], 'final')
        super().generate_profile()

    def place_vals(self, idx, base_arg):
        for entity, config in self.args[base_arg].items():
            if 'color_name' in config:
                vals = self.color_dict.loc[config['color_name']].values
            elif self.val_kwarg in config:
                vals = config[self.val_kwarg]

            try:
                self.profile.loc[idx, pd.IndexSlice[entity, self.cols]] = [float(v) for v in vals]
            except KeyError as e:
                self.log(f'{entity} not in {self.profile.columns.get_level_values(level=0)}')

            if 'brightness_pct' in config:
                self.profile.loc[idx, (entity, 'brightness_pct')] = config['brightness_pct']

    def operate(self, kwargs=None):
        idx = self.get_last_index()
        self.log(f'Operating step {idx} at {self.profile.index[idx]}')
        for entity, config in self.profile.iloc[idx].groupby(level=0):
            current_state = self.get_state(entity)
            if current_state == 'on':
                kwargs = {self.val_kwarg: [config[(entity, color)] for color in self.cols]}
                if (b := config[entity].get('brightness_pct')):
                    kwargs['brightness_pct'] = b
                self.turn_on(entity_id=entity, **kwargs)
                self.log(f'Adjusted\n{entity}\n{kwargs}')
            elif current_state == 'off':
                self.log(f'{entity} off, skipping')
        super().operate(kwargs)


class SceneFader(PandasCtl):
    """
    Arguments
        start:
        end:
        force_initial:
        initial:
        final:
        weekday:
    """
    def initialize(self):
        self.validate_args()
        super().initialize()

    def read_scene_config(self):
        with Path(self.args.get('ha_config', f'/usr/homeassistant/scenes.yaml')).open('r') as file:
            return {scene['name']: scene['entities'] for scene in yaml.load(file, Loader=yaml.SafeLoader)}

    def generate_profile(self):
        scenes = self.read_scene_config()
        self.profile = self.blank_df(entities=scenes[self.args['initial']].keys(), attributes=['brightness_pct', 'color_temp'])
        start, end = self.profile.index[[0, -1]]
        for entity in self.profile.columns.get_level_values(0):
            for attr, val in scenes[self.args['initial']][entity].items():
                if attr != 'state':
                    self.profile.loc[start, (entity, attr)] = float(val)
            for attr, val in scenes[self.args['final']][entity].items():
                if attr != 'state':
                    self.profile.loc[end, (entity, attr)] = float(val)
        super().generate_profile()
        # self.log(f'Profile\n{self.profile.columns}\n{self.profile.index}')

    def operate(self, kwargs=None):
        idx = self.get_last_index()
        self.log(f'Operating step {idx} at {self.profile.index[idx]}')

        if idx == self.profile.index[0]:
            scene = self.args["initial"].lower().replace(" ", "_")
            self.turn_on(f'scene.{scene}')
            self.log(f'Activated scene.{scene}')
        elif idx == self.profile.index[-1]:
            scene = self.args["final"].lower().replace(" ", "_")
            self.turn_on(f'scene.{scene}')
            self.log(f'Activated scene.{scene}')

        for entity, config in self.profile.iloc[idx].groupby(level=0):
            current_state = self.get_state(entity)
            if current_state == 'on':
                attrs = config.droplevel(0).to_dict()
                self.turn_on(entity_id=entity, **attrs)
                self.log(f'Adjusted\n{entity}\n{attrs}')
            elif current_state == 'off':
                self.log(f'{entity} off, skipping')

        super().operate(kwargs)
