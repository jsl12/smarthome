from datetime import datetime, time, timedelta

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


class GrowLight(hass.Hass):
    def initialize(self):
        """

        self.args:
            entity_id:
            start:
            end:
            duration:

        Returns
        -------

        """
        self.target = self.args['entity_id']

        hours, minutes, seconds = map(int, self.args['duration'].split(':'))
        self.total_duration: timedelta = timedelta(hours=hours, minutes=minutes, seconds=seconds)

        self.start_time: time = self.parse_time(self.args['start'])
        if (t := self.args.get('end', None)) is not None:
            self.force_end_time: time = self.parse_time(t)
            self.force_end_timer = self.run_daily(self.end_growtime, start=self.force_end_time)

        self.daily_timer = self.run_daily(self.start_growtime, self.start_time)

        if self.during_growtime:
            self.start_growtime()
        else:
            self.turn_off(self.target)

    @property
    def start_datetime(self) -> datetime:
        return datetime.combine(self.date(), self.start_time).astimezone()

    @property
    def current_datetime(self) -> datetime:
        return self.datetime().astimezone()

    @property
    def natural_end_datetime(self) -> datetime:
        return self.current_datetime + self.remaining_growtime

    @property
    def force_end_datetime(self) -> datetime:
        return datetime.combine(self.date(), self.force_end_time).astimezone()

    @property
    def during_growtime(self):
        return self.start_time < self.time() < self.force_end_time

    @property
    def remaining_growtime(self) -> timedelta:
        if hasattr(self, 'previously_accrued'):
            return self.total_duration - self.previously_accrued
        else:
            return self.total_duration

    def start_growtime(self, kwargs=None):
        self.log(f'Starting {self.total_duration} of daily grow time')
        self.on_event = self.listen_state(self.handle_on, entity=self.target, new='on')
        self.off_event = self.listen_state(self.handle_off, entity=self.target, new='off')

        # if the end will naturally occur before the force end
        if self.natural_end_datetime <= self.force_end_datetime:
            # schedule the end_growtime event
            self.natural_end_timer = self.run_in(
                callback=self.end_growtime,
                delay=int(round(self.remaining_growtime.total_seconds(), 0))
            )
            self.log(f'Scheduled natural end for {self.natural_end_datetime.strftime("%I:%M:%S%p").lower()}')
        else:
            self.log(f'Force end at {self.force_end_time.strftime("%I:%M:%S%p").lower()} happens before '
                     f'natural end at {self.natural_end_datetime.strftime("%I:%M:%S%p").lower()}')

        self.previously_accrued = timedelta()

        if self.get_state(self.target) == 'off':
            self.turn_on(self.target)
        elif self.get_state(self.target) == 'on':
            self.handle_on()
        else:
            self.log(f'{self.target} unavailable')

    def end_growtime(self, kwargs=None):
        self.log(f'Ending grow time on {self.target}')
        self.turn_off(self.target)
        self.terminate()

    def handle_on(self, entity=None, attribute=None, old=None, new=None, kwargs=None):
        if self.during_growtime and not hasattr(self, 'on_datetime'):
            self.on_datetime: datetime = self.current_datetime
            self.log(f'Updated on_datetime {self.on_datetime.strftime("%H:%M:%S")}')
            self.log(f'{self.friendly_name(self.target)} turned on, previously accrued: {self.previously_accrued}')
        else:
            if not self.during_growtime:
                self.log(f'{self.friendly_name(self.target)} turned on outside of grow time')
            elif hasattr(self, 'on_datetime'):
                self.log(f'GrowLight on {self.friendly_name(self.target)} already has an on_datetime at '
                         f'{self.on_datetime.strftime("%I:%M:%S%p").lower()}')

    def handle_off(self, entity=None, attribute=None, old=None, new=None, kwargs=None):
        if self.during_growtime and hasattr(self, 'on_datetime'):
            self.log(f'{self.friendly_name(self.target)} turned off, accrued {self.previously_accrued}')

            newly_accrued: timedelta = self.current_datetime - self.on_datetime

            del self.on_datetime
            self.log(f'Removed on_datetime')

            self.previously_accrued += newly_accrued
            self.log(f'Added {newly_accrued}, total {self.previously_accrued}')
        else:
            if not hasattr(self, 'on_datetime'):
                self.log(f'{self.friendly_name(self.target)} turned off without an on_datetime')

    def terminate(self):
        self.log(f'Cleaning up callbacks')

        listen_state_events = ['on_event', 'off_event']
        for ev in listen_state_events:
            if (handle := getattr(self, ev, None)):
                try:
                    self.cancel_listen_state(handle)
                    delattr(self, ev)
                except Exception as e:
                    self.log(f'{e} while cancelling {ev}')

        schedule_events = ['daily_timer', 'natural_end_timer', 'force_end_timer']
        for ev in schedule_events:
            if (handle := getattr(self, ev, None)):
                try:
                    self.cancel_timer(handle)
                    delattr(self, ev)
                except Exception as e:
                    self.log(f'{e} while cancelling {ev}')
