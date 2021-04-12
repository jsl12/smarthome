from datetime import datetime
from pathlib import Path
from typing import List

from appdaemon.plugins.hass import hassapi as hass

import colors
import helpers


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
