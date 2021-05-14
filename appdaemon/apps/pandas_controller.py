from datetime import datetime
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
        self.timer_start = self.run_daily(callback=self.start, start=self.args['start'])
        if self.active:
            self.start()

    def start(self, kwargs = None):
        self.idx = self.index()
        self.operate({'idx': self.closest_index()})

    def operate(self, kwargs = None):
        try:
            next_operation: datetime = self.idx.to_pydatetime()[self.idx.get_loc(kwargs['idx']) + 1]
        except IndexError as e:
            self.log(f'Ending')
        else:
            self.run_at(callback=self.operate, start=next_operation.strftime('%H:%M:%S'), idx=next_operation)

    def terminate(self):
        if hasattr(self, 'timer_start'):
            self.cancel_timer(self.timer_start)

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


class SunriseLight(IndexShifter):
    def initialize(self):
        super(SunriseLight, self).initialize()
        assert 'entity_id' in self.args

    def start(self, kwargs=None):
        self.profile = pd.DataFrame(columns=['color_temp', 'brightness_pct'],
                                    index=self.index())
        self.profile.iloc[[0, -1]] = [[200, 1], [350, 50]]
        self.profile = (
            self
            .profile
            .applymap(float)
            .interpolate('time', axis='index')
            .applymap(round)
            .applymap(int)
            .drop_duplicates()
            .sort_index(axis=1)
        )
        self.idx = self.profile.index
        self.turn_on(self.entity)
        super(SunriseLight, self).start(kwargs)

    @property
    def entity(self):
        return self.args['entity_id']

    def operate(self, kwargs = None):
        attributes = self.profile.loc[kwargs['idx']].to_dict()
        if self.get_state(self.entity) == 'on':
            self.turn_on(self.entity, **attributes)
            self.log(f"{self.entity}: {attributes}")
        else:
            self.log(f'{self.entity} is off')
        super(SunriseLight, self).operate(kwargs)


class PandasCtl(IndexShifter):
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

    def initialize(self):
        super(PandasCtl, self).initialize()

        self.colors = colors.get_colors(self.COLOR_YAML)

        self.start_timer = self.run_daily(callback=self.operate, start=self.args['start'])
        self.log(f'Operation initially scheduled for {self.parse_time(self.args["start"])}')

        if self.active:
            self.generate_profile()
            last_action_time = self.profile.index[self.prev_index].time().isoformat()[:8]
            self.log(f'Already supposed to be active, starting. Last action: {last_action_time}')
            self.operate()

    def generate_profile(self):
        """
        Handles generating the operation profile which is a `pd.DataFrame` object

        Returns
        -------

        """
        self.profile = helpers.scene_df(
            start=self.start_datetime,
            end=self.stop_datetime,
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
