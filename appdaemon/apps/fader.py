from datetime import datetime, time
from pathlib import Path

import pandas as pd
import yaml
from appdaemon.plugins.hass import hassapi as hass


class TimeFader(hass.Hass):
    def initialize(self):
        self.target = self.args['entity_id']
        assert self.start_datetime <= self.end_datetime

        self.initial_conditions = self.args['initial']
        self.final_conditions = self.args['final']
        self.unique_args = {attr: value
                            for attr, value in self.initial_conditions.items()
                            if attr not in self.final_conditions}

        self.log(
            f'TimeFader on {self.friendly_name(self.target)} '
            f'from {self.start_datetime.strftime("%I:%M%p").lower()} to '
            f'{self.end_datetime.strftime("%I:%M%p").lower()}'
        )

        self.daily_timer = self.run_daily(callback=self.start_fade_time,
                                          start=self.args['start'])

        if self.fade_active:
            self.start_fade_time()

    def calculate_slopes(self):
        self.slopes = {}
        for arg in self.initial_conditions:
            if arg in self.final_conditions:
                value_range = self.final_conditions[arg] - self.initial_conditions[arg]
                slope = value_range / (self.end_datetime - self.start_datetime).total_seconds()
                self.slopes[arg] = slope

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
    def fade_active(self) -> bool:
        return self.end_datetime >= self.current_datetime >= self.start_datetime

    def formatted_time(self):
        return self.time().strftime("%I:%M:%S%p").lower()

    def start_fade_time(self, kwargs = {}):
        self.log(f'Starting TimeFade on {self.friendly_name(self.target)}')

        self.log(f'Calculating attribute slopes')
        self.calculate_slopes()

        if bool(self.args.get('force_on', None)):
            self.log(f'Forcing initial conditions on {self.target}')
            self.turn_on(self.target, **self.unique_args)

        self.adjust_timer = self.run_minutely(callback=self.adjust,
                                              start=time(0, 0, 0))
        # self.override_state = self.listen_state(callback=self.handle_state_change,
        #                                         entity=self.target,
        #                                         attribute='all')

        self.adjust()

    def handle_state_change(self, entity, attribute, old, new, *args):
        if hasattr(self, 'last_kwargs') and all(new[attr] == value for attr, value in self.last_kwargs.items() if attr in new):
            self.log(f'{entity} state changed')

    def calculate_elapsed(self):
        return (self.current_datetime - self.start_datetime).total_seconds()

    @property
    def enabled(self):
        target_is_on: bool = (self.get_state(self.target) == 'on')
        if 'constrain_input_boolean' in self.args:
            input_boolean_enabled: bool = (self.get_state(self.args['constrain_input_boolean']) == 'on')
            return (input_boolean_enabled and target_is_on)
        else:
            return target_is_on

    def calculated_attributes(self):
        kwargs = self.initial_conditions.copy()
        elapsed_time = self.calculate_elapsed()
        calc = lambda arg, slope: int(round((slope * elapsed_time) + self.initial_conditions[arg], 0))
        kwargs.update({arg: calc(arg, slope) for arg, slope in self.slopes.items()})
        return kwargs

    def adjust(self, kwargs = {}):
        if self.enabled:
            kwargs = self.calculated_attributes()
            # stops from sending repeat commands
            if not (hasattr(self, 'last_kwargs') and kwargs == self.last_kwargs):
                self.log(f'{self.target} {kwargs}')
                self.last_kwargs = kwargs
                self.turn_on(entity_id=self.target, **kwargs)

        if self.time() >= self.end_datetime.time():
            self.log(f'Ending TimeFader at {self.formatted_time()}')
            self.terminate()

    def terminate(self):
        if hasattr(self, 'daily_timer'):
            self.cancel_timer(self.daily_timer)
            del self.daily_timer

        if hasattr(self, 'adjust_timer'):
            self.cancel_timer(self.adjust_timer)
            del self.adjust_timer

        if hasattr(self, 'override_state'):
            self.cancel_listen_state(self.override_state)
            del self.override_state

class SceneFader(hass.Hass):
    """
    Arguments
        start
        end
        initial:
        final:

    """
    def initialize(self):
        assert self.start_datetime <= self.end_datetime
        self.log(
            f'SceneFader {self.args["initial"]} to {self.args["final"]} '
            f'from {self.start_datetime.strftime("%I:%M%p").lower()} to '
            f'{self.end_datetime.strftime("%I:%M%p").lower()}'
        )

        self.daily_timer = self.run_daily(callback=self.start_fade,
                                          start=self.args['start'])

        self.ha_config = Path(self.args.get('ha_config', f'/usr/homeassistant/scenes.yaml'))

        if self.active:
            self.start_fade()

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

    def start_fade(self, kwargs=None):
        self.log(f'Starting Scene fade, calculating profile')
        self.profile = profile_from_scenes(scene_path=self.ha_config,
                                           initial=self.args['initial'],
                                           final=self.args['final'],
                                           start=self.start_datetime,
                                           end=self.end_datetime)
        self.log(f'From {self.profile.index[0].time().isoformat()[:8]} to {self.profile.index[-1].time().isoformat()[:8]}')
        first_time = self.profile.index[self.profile.index >= self.current_datetime][0].to_pydatetime().time().isoformat()[:8]
        self.log(f'Starting adjust callbacks at {first_time}')
        # self.adjust_timer = self.run_at(callback=self.adjust, start=first_time)
        self.adjust_timer = self.run_minutely(callback=self.adjust, start=time.min)

    def adjust(self, kwargs):
        for ent, s in self.profile[self.profile.index <= self.current_datetime].groupby(level=0, axis=1):
            s.columns = s.columns.droplevel(0)
            s = s.iloc[-1].to_dict()
            state = s.pop('state')
            if state == 'on':
                self.log(f'{ent:20} {s}')
                self.turn_on(entity_id=ent, **s)
            elif state == 'off':
                self.log(f'{ent:20} off')
                self.turn_off(entity_id=ent)


    def terminate(self):
        try:
            self.cancel_timer(self.adjust_timer)
        except:
            pass

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

    cols = df.iloc[[0, -1]].loc[:, pd.IndexSlice[:, 'state']].dropna(how='all', axis=1).columns
    ffill_cols = df.loc[:, cols].loc[:, ~pd.isna(df.loc[:, cols].iloc[0])].columns
    # bfill_cols = df.loc[:, cols].loc[:, pd.isna(df.loc[:, cols].iloc[0])].columns

    df.loc[:, ffill_cols] = df.loc[:, ffill_cols].fillna(method='ffill')
    #     df.loc[:, bfill_cols] = df.loc[:, bfill_cols].fillna(method='bfill')

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
