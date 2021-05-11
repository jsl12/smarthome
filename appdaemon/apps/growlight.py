from datetime import datetime, time, timedelta

from appdaemon.plugins.hass import hassapi as hass


class GrowLight(hass.Hass):
    def initialize(self):
        """

        self.args:
            entity_id:
            start:
            duration: 00:00:00
            end:

        Returns
        -------

        """
        assert 'start' in self.args and 'entity_id' in self.args
        assert 'end' in self.args or 'duration' in self.args

        self.target = self.args['entity_id']
        self.start_timer = self.run_daily(callback=self.start_growtime, start=self.args['start'])
        if (end := self.args.get('end', False)):
            self.stop_timer = self.run_daily(callback=self.stop_growtime, start=end)

        if self.during_growtime:
            self.start_growtime()
        else:
            self.turn_off(self.target)

    @property
    def during_growtime(self) -> bool:
        return self.start_datetime < self.current_datetime < self.stop_datetime

    @property
    def current_datetime(self) -> datetime:
        return self.datetime().astimezone()

    @property
    def start_datetime(self) -> datetime:
        return datetime.combine(self.date(), self.parse_time(self.args['start'])).astimezone()

    @property
    def stop_datetime(self) -> datetime:
        if (end := self.args.get('end', False)):
            return datetime.combine(self.date(), self.parse_time(end)).astimezone()
        elif (duration := self.args.get('duration', False)):
            hours, minutes, seconds = map(int, duration.split(':'))
            return self.start_datetime + timedelta(hours=hours, minutes=minutes, seconds=seconds)

    def start_growtime(self, kwargs=None):
        self.log('Starting growtime')
        if self.get_state(self.target) == 'off':
            self.turn_on(self.target)
        else:
            self.log(f'{self.target} unavailable')

        if (duration := self.args.get('duration', False)):
            self.end_timer = self.run_at(self.stop_growtime, self.stop_datetime.time().strftime('%H:%M:%S'))

    def stop_growtime(self, kwargs=None):
        state = self.get_state(self.target)
        if state == 'on':
            self.log(f'Ending grow time on {self.target}')
            self.turn_off(self.target)
        elif state == 'off':
            self.log(f'{self.target} already off at end of grow time')
