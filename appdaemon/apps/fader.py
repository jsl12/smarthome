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
        required = ['start', 'end', 'initial', 'final']
        for r in required:
            assert r in self.args

        assert self.start_datetime < self.end_datetime, f'{self.start_datetime:19} is not before {self.end_datetime:19}'

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
        self.profile = self.blank_df(cols=['red', 'green', 'blue'])
        self.place_vals(self.profile.index[0], 'initial')
        self.place_vals(self.profile.index[-1], 'final')
        self.interpolate()

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


class SceneFader(hass.Hass):
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
        assert self.start_datetime <= self.end_datetime
        self.log(
            f'SceneFader {self.args["initial"]} to {self.args["final"]} '
            f'from {self.start_datetime.strftime("%I:%M%p").lower()} to '
            f'{self.end_datetime.strftime("%I:%M%p").lower()}'
        )

        # this will actually fire self.start_fade
        self.daily_timer = self.run_daily(callback=self.start_fade,
                                          start=self.args['start'])

        self.ha_config = Path(self.args.get('ha_config', f'/usr/homeassistant/scenes.yaml'))

    def datetime_arg(self, arg_name: str):
        return datetime.combine(self.date(), self.parse_time(self.args[arg_name])).astimezone()

    @property
    def start_datetime(self) -> datetime:
        return self.datetime_arg('start')

    @property
    def current_datetime(self) -> datetime:
        return self.datetime().astimezone()

    @property
    def end_datetime(self) -> datetime:
        return self.datetime_arg('end')

    @property
    def active(self) -> bool:
        return self.end_datetime >= self.current_datetime >= self.start_datetime

    @property
    def entities(self):
        return self.profile.columns.levels[0].values

    def entity_profile(self, entity) -> pd.DataFrame:
        return self.profile.loc[:, pd.IndexSlice[entity, :]].dropna(how='all').drop_duplicates()

    @property
    def prev_step(self):
        return self.profile.iloc[self.i - 1]

    @property
    def this_step(self):
        return self.profile.iloc[self.i]

    @property
    def this_step_time(self, ) -> str:
        return self.this_step.name.to_pydatetime().time().isoformat()[:8]

    def start_fade(self, kwargs=None):
        if (days := self.args.get('weekday', False)):
            if (day := self.current_datetime.date().strftime('%a').lower()) not in days:
                self.log(f'Not valid for {day}')
                return

        self.log(f'Starting Scene fade, calculating profile')
        self.profile = profile_from_scenes(scene_path=self.ha_config,
                                           initial=self.args['initial'],
                                           final=self.args['final'],
                                           start=self.start_datetime,
                                           end=self.end_datetime)

        if self.args.get('force_initial', False):
            self.log('Forcing the initial on/off initial_state')
            for entity, initial_state in self.profile.iloc[0].loc[pd.IndexSlice[:, 'state']].iteritems():
                if not pd.isna(initial_state):
                    self.log(f'{entity:20} {initial_state}')
                if initial_state == 'on':
                    self.turn_on(entity)
                elif initial_state == 'off':
                    self.turn_off(entity)

        self.i = s - 1 if (s := (self.profile.index <= self.current_datetime).sum()) > 0 else 0

        self.log(
            f'Index from {self.profile.index[0].time().isoformat()[:8]} to '
            f'{self.profile.index[-1].time().isoformat()[:8]}, '
            f'step {self.i+1}/{self.profile.shape[0]}'
        )
        self.adjust()

    def adjust(self, kwargs=None):
        self.log(f'Adjusting step {self.i+1} at {self.profile.index[self.i].time().isoformat()[:8]}')
        for entity, profile_state in self.profile.iloc[self.i].dropna().loc[pd.IndexSlice[:, 'state']].iteritems():
            if self.get_state(entity) == 'on':
                if profile_state == 'on':
                    kwargs = self.profile.iloc[self.i].loc[entity].drop('state').to_dict()
                    self.log(f'{entity:20} {profile_state} {kwargs}')
                    self.turn_on(entity_id=entity, **kwargs)
                elif profile_state == 'off':
                    self.log(f'{entity:20} turned off')
                    self.turn_off(entity_id=entity)
            else:
                self.log(f'{entity:20} skipped, already off')

        if self.active:
            self.start_next()

    def start_next(self):
        self.i += 1
        try:
            next_time = self.this_step_time
        except IndexError:
            self.log(f'No next adjust time, last at {self.profile.index[-1].time().isoformat()[:8]}')
        else:
            self.log(f'Next adjustment at {next_time}')
            self.adjust_timer = self.run_at(callback=self.adjust, start=next_time, pin_thread=4)

    def cancel_adjust(self):
        try:
            self.cancel_timer(self.adjust_timer)
        except:
            pass
        else:
            del self.adjust_timer

    def terminate(self):
        self.cancel_adjust()

        try:
            self.cancel_timer(self.daily_timer)
        except:
            pass


def profile_from_scenes(scene_path, initial, final, start: datetime, end: datetime) -> pd.DataFrame:
    with Path(scene_path).open('r') as file:
        scenes = {s['name']:s for s in yaml.load(file, Loader=yaml.SafeLoader)}
    return create_profile(initial=scenes[initial]['entities'],
                          final=scenes[final]['entities'],
                          start=start,
                          end=end)


def create_profile(initial, final, start: datetime, end: datetime) -> pd.DataFrame:
    df = pd.DataFrame(
        columns=pd.MultiIndex.from_product([
            pd.Index(initial.keys()).union(final.keys()).values,
            ['state', 'brightness_pct', 'color_temp']
        ]),
        index=pd.date_range(start=start, end=end, freq='1min')
    )

    for e, attrs in initial.items():
        for a, val in attrs.items():
            df.iloc[0].loc[e, a] = val

    for e, attrs in final.items():
        for a, val in attrs.items():
            df.iloc[-1].loc[e, a] = val

    states = df.loc[:, pd.IndexSlice[:, 'state']]
    initially_on = states.iloc[0] == 'on'
    ffill_cols = states.loc[:, initially_on].columns
    df.loc[:, ffill_cols] = df.loc[:, ffill_cols].fillna(method='ffill')

    fill_numeric(df, 'brightness_pct')
    fill_numeric(df, 'color_temp')
    return df[~df.duplicated(keep='first')]


def fill_numeric(df: pd.DataFrame, attr: str) -> pd.DataFrame:
    cols = df.iloc[[0, -1]].loc[:, pd.IndexSlice[:, attr]].dropna(how='all', axis=1).columns
    inter_cols = df.iloc[[0, -1]].loc[:, pd.IndexSlice[:, attr]].dropna(how='any', axis=1).columns
    ffill_cols = df[cols].loc[:, pd.isna(df[cols].iloc[-1])].columns
    bfill_cols = df[cols].loc[:, pd.isna(df[cols].iloc[0])].columns
    df.loc[:, inter_cols] = df.loc[:, inter_cols].applymap(float).interpolate('linear').applymap(round).applymap(int)
    df.loc[:, ffill_cols] = df[ffill_cols].fillna(method='ffill')
    df.loc[:, bfill_cols] = df[bfill_cols].fillna(method='bfill')
    return df
