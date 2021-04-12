from pathlib import Path
from typing import List, Dict

import pandas as pd
import yaml

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

    @property
    def entities(self):
        return [entity_id for entity_id, attributes in self.args['initial'].items() if entity_id.split('.')[0] == 'light']

    @property
    def attributes(self):
        return ['rgb_color']

    def populate(self):
        self.place_vals(self.profile.index[0], self.args['initial'])
        self.place_vals(self.profile.index[-1], self.args['final'])

    def place_vals(self, idx, config):
        for entity, config in config.items():
            if (color := config.get('color_name', False)):
                vals = self.colors.loc[color].values
            elif (color_vals := config.get('rgb_color', False)):
                vals = color_vals

            cols = ['red', 'green', 'blue']
            df = self.blank_df([entity], cols)

            try:
                df.loc[idx, pd.IndexSlice[entity, cols]] = [float(v) for v in vals]
            except KeyError as e:
                self.log(f'{entity} not in {self.profile.columns.get_level_values(level=0)}')
            except ValueError as e:
                self.log(f'ValueError: {e}\n{str(self.profile.loc[idx, pd.IndexSlice[entity, self.cols]])}\n{[float(v) for v in vals]}')
            else:
                df = self.interpolate(df)
                df[idx, pd.IndexSlice[entity, 'rgb_color']] = df.apply(lambda row: row.to_list(), axis=1)
                df = df.drop(cols, axis=1)
                self.profile = df

            if brightness := config.get('brightness_pct', False):
                self.profile.loc[idx, (entity, 'brightness_pct')] = brightness

    def operate(self, kwargs=None):
        idx = self.prev_index
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
    @property
    def initial_scene(self):
        return self.read_scene_config()[self.args['initial']]

    @property
    def final_scene(self):
        return self.read_scene_config()[self.args['final']]

    @property
    def entities(self) -> List[str]:
        return [ent for ent, attrs in self.initial_scene.items()] + \
               [ent for ent, attrs in self.final_scene.items()]

    @property
    def attributes(self) -> List[str]:
        return [attr for ent, attrs in self.initial_scene.items() for attr, val in attrs.items()] + \
               [attr for ent, attrs in self.final_scene.items() for attr, val in attrs.items()]

    def read_scene_config(self) -> Dict:
        with Path(self.args.get('ha_config', f'/usr/homeassistant/scenes.yaml')).open('r') as file:
            return {scene['name']: scene['entities'] for scene in yaml.load(file, Loader=yaml.SafeLoader)}

    def populate(self):
        self.place_val(self.profile.index[0], self.initial_scene)
        self.place_val(self.profile.index[-1], self.final_scene)

    def place_val(self, idx, scene):
        for entity, attrs in scene.items():
            for attr, val in attrs.items():
                if attr != 'state':
                    self.profile.loc[idx, (entity, attr)] = float(val)

    def operate(self, kwargs=None):
        idx = self.prev_index
        self.log(f'Operating step {idx} at {self.profile.index[idx]}')

        if idx == self.profile.index[0]:
            scene = self.args["initial"].lower().replace(" ", "_")
            self.turn_on(f'scene.{scene}')
            self.log(f'Activated scene.{scene}')
        elif idx == self.profile.index[-1]:
            scene = self.args["final"].lower().replace(" ", "_")
            self.turn_on(f'scene.{scene}')
            self.log(f'Activated scene.{scene}')
        else:
            log_str = ['Operation:']
            for entity, config in self.profile.iloc[idx].groupby(level=0):
                current_state = self.get_state(entity)
                if current_state == 'on':
                    attrs = config.droplevel(0).to_dict()
                    self.turn_on(entity_id=entity, **attrs)
                    log_str.append(f'{entity}: {attrs}')
                elif current_state == 'off':
                    self.log(f'{entity} off, skipping')
                    return
                self.log('\n'.join(log_str))

        super().operate(kwargs)
