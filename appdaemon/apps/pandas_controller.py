from datetime import datetime
from pathlib import Path

from appdaemon.plugins.hass import hassapi as hass

import pandas as pd

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
    """
    def validate_args(self):
        required = ['start', 'end', 'initial', 'final']
        for r in required:
            assert r in self.args

        assert self.start_datetime < self.end_datetime, f'{self.start_datetime:19} is not before {self.end_datetime:19}'

    def initialize(self):
        self.start_timer = self.run_daily(callback=self.operate, start=self.args['start'])
        self.log(f'Operation scheduled for {self.parse_time(self.args["start"])}')

        if self.active:
            self.generate_profile()
            last_action = self.profile.index[self.get_last_index()].time().isoformat()[:8]
            self.log(f'Already supposed to be active, starting. Last action: {last_action}')
            self.operate()

    def generate_profile(self):
        self.interpolate()
        if (p := self.args.get('profile', None)) is not None:
            self.profile.to_csv(Path(p).with_suffix('.csv'))

    def blank_df(self, entities, attributes, freq='1min'):
        return pd.DataFrame(
            columns=pd.MultiIndex.from_product([entities, attributes]),
            index=pd.date_range(start=self.start_datetime,
                                end=self.end_datetime,
                                freq=self.args.get('freq', freq)).round('S')
        )

    def interpolate(self):
        for entity, profile in self.profile.groupby(level=0, axis=1):
            df = (
                profile
                    .droplevel(0, axis=1)
                    .applymap(float)
                    .interpolate('time', axis='index')
                    .applymap(round)
                    .applymap(int)
            )
            self.profile.loc[:, pd.IndexSlice[entity, :]] = df.values

        self.profile = self.profile.drop_duplicates().sort_index(axis=1)

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

    def get_last_index(self):
        return self.get_next_index() - 1

    def get_next_index(self):
        return (self.profile.index <= self.current_datetime).sum()

    def operate(self, kwargs=None):
        try:
            next_operation_time = self.profile.index[self.get_next_index()].time().isoformat()[:8]
        except IndexError as e:
            self.log(f'Finished operation')
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
