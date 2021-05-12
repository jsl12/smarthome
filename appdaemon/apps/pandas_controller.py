from datetime import datetime, timedelta
from pathlib import Path
from typing import List

import pandas as pd
from appdaemon.plugins.hass import hassapi as hass

import colors
import helpers


class IndexShifter(hass.Hass):
    def initialize(self):
        assert 'start' in self.args

        self.log(f'Starting at {self.args["start"]} {self.start_datetime.time()}')

        if self.args['start'].lower().startswith('sunrise'):
            self.timer_start = self.run_at_sunrise(callback=self.start, offset=self.start_offset)
        elif self.args['start'].lower().startswith('sunset'):
            self.timer_start = self.run_at_sunset(callback=self.start, offset=self.start_offset)
        else:
            self.timer_start = self.run_daily(callback=self.start, start=self.start_datetime.time())

        if self.active:
            self.start()

    def start(self, kwargs = None):
        self.idx = self.index()
        self.operate({'idx': self.closest_index()})

    def operate(self, kwargs):
        idx = kwargs['idx']
        self.log(f'Operating: {idx}')
        try:
            closest_next: datetime = self.idx.to_pydatetime()[self.idx.get_loc(idx) + 1]
        except IndexError as e:
            self.log(f'Ending')
        else:
            self.run_at(callback=self.operate, start=closest_next.strftime('%H:%M:%S'), idx=closest_next)
            self.log(f'Next run: {closest_next.time()}')

    def terminate(self):
        if hasattr(self, 'timer_start'):
            self.cancel_timer(self.timer_start)

    @property
    def start_offset(self) -> int:
        try:
            sun, sign, offset = self.args['start'].split()
        except:
            return
        else:
            hours, minutes, seconds = map(int, offset.split(':'))
            offset = int(timedelta(hours=hours, minutes=minutes, seconds=seconds).total_seconds())
            if sign == '+':
                return offset
            elif sign == '-':
                return -offset
            else:
                raise ValueError(f'Invalid sign: {sign}')

    @property
    def start_datetime(self) -> datetime:
        return datetime.combine(self.date(), self.parse_time(self.args['start'])).astimezone()

    @property
    def stop_datetime(self) -> datetime:
        return datetime.combine(self.date(), self.parse_time(self.args['stop'])).astimezone()

    @property
    def current_datetime(self) -> datetime:
        return self.datetime().astimezone()

    @property
    def active(self) -> bool:
        return self.start_datetime <= self.current_datetime <= self.stop_datetime

    def index(self) -> pd.Index:
        return pd.date_range(start=self.start_datetime,
                             end=self.stop_datetime,
                             freq=self.args.get('freq', '1 min')).round('S')

    def closest_index(self) -> datetime:
        return (self.idx.to_series() - self.current_datetime).apply(
            lambda td: abs(td.total_seconds())
        ).sort_values().index.to_series()[0].to_pydatetime()


class PandasCtl(hass.Hass):
    """
    Args:
        start:
        end:
        initial:
            entity_id:
        final:
            entity_id:
        [freq: 1min]
        [profile: <path>]

    Required overrides:
        self.validate_args()
        self.generate_profile()
        self.populate()
        self.entities
        self.attributes
    """
    COLOR_YAML: Path = colors.YAML_PATH

    @property
    def entities(self) -> List[str]:
        raise NotImplementedError

    @property
    def start_datetime(self) -> datetime:
        return datetime.combine(self.date(), self.parse_time(self.args['start'])).astimezone()

    @property
    def current_datetime(self) -> datetime:
        return self.datetime().astimezone()

    @property
    def end_datetime(self) -> datetime:
        return datetime.combine(self.date(), self.parse_time(self.args['end'])).astimezone()

    @property
    def active(self):
        return self.start_datetime <= self.current_datetime <= self.end_datetime

    @property
    def prev_index(self):
        idx = self.next_index
        return 0 if idx <= 0 else idx

    @property
    def next_index(self):
        try:
            return (self.profile.index <= self.current_datetime).sum()
        except AttributeError:
            self.generate_profile()
            return 0

    def initialize(self):
        self.validate_args()

        self.colors = colors.get_colors(self.COLOR_YAML)

        self.start_timer = self.run_daily(callback=self.operate, start=self.args['start'])
        self.log(f'Operation initially scheduled for {self.parse_time(self.args["start"])}')

        if self.active:
            self.generate_profile()
            last_action_time = self.profile.index[self.prev_index].time().isoformat()[:8]
            self.log(f'Already supposed to be active, starting. Last action: {last_action_time}')
            self.operate()

    def validate_args(self):
        required = ['start', 'end', 'initial', 'final']
        for r in required:
            assert r in self.args

        assert self.start_datetime < self.end_datetime, f'{self.start_datetime:19} is not before {self.end_datetime:19}'

    def generate_profile(self):
        """
        Handles generating the operation profile which is a `pd.DataFrame` object

        Returns
        -------

        """
        self.profile = helpers.scene_df(
            start=self.start_datetime,
            end=self.end_datetime,
            freq='1min',
            entities=self.entities,
            config_dir=helpers.HOMEASSISTANT_CONFIG_DIR
        )

        try:
            # modify self.profile with the initial values
            self.populate()
        except Exception as e:
            self.log(f'Error populating profile on {self.name}')
            raise
        else:
            try:
                self.profile = helpers.interpolate(self.profile)
            except Exception as e:
                self.log(f'Error interpolating with {self.name}')
                raise
            else:
                if (profile_path := self.args.get('profile', None)) is not None:
                    dest = Path(profile_path).with_suffix('.csv')
                    self.log(f'Saving profile to {dest}')
                    self.profile.to_csv(dest)
        self.log('Columns:\n' + '\n'.join(str(c) for c in self.profile.columns))

    def populate(self):
        raise NotImplementedError

    def operate(self, kwargs=None):
        """
        Only handles scheduling the next operation. All other logic needs to happen in the overridden
        method.

        Parameters
        ----------
        kwargs :

        Returns
        -------

        """
        try:
            next_operation_time = self.profile.index[self.next_index].time().isoformat()[:8]
        except IndexError as e:
            self.log(f'Finished operation')
            del self.profile
        else:
            try:
                self.operate_timer = self.run_at(callback=self.operate, start=next_operation_time)
            except ValueError as e:
                self.log(f'{next_operation_time} is not in the future, not scheduled')
            else:
                self.log(f'Next operation at {next_operation_time}')

    def terminate(self):
        if hasattr(self, 'start_time'):
            self.cancel_timer(self.start_timer)
