# switch_controller.py
import logging
import mpf.timing
import math
#import mpf.events
from collections import defaultdict


class SwitchController(object):
    def __init__(self, machine):
        self.log = logging.getLogger('SwitchController')
        self.machine = machine
        self.registered_switches = defaultdict(list)
        self.active_timed_switches = defaultdict(list)
        self.switches = {}  # 'state' here factors in NC or NO. So 1 is active.

        # register for events
        self.machine.events.add_handler('timer_tick', self.tick)
        self.machine.events.add_handler('machine_init_complete',
                                        self.initialize_hw_states,
                                        1000)
                                        # priority 1000 so this fires first

    def initialize_hw_states(self):
        """ Reads in all the hw states of the switches. We can't do this in
        __init__ because we need the switch controller to be setup first before
        we set up the hw switches.
        """

        if self.machine.physical_hw:
            for switch in self.machine.switches:
                if switch.type == 'NO':
                    if switch.state:
                        state = 1
                    else:
                        state = 0
                elif switch.type == 'NC':
                    if switch.state:
                        state = 0
                    else:
                        state = 1
                self.switches.update({switch.name: {'state': state,
                                                    'time': 0}})
        else:
            # If we don't have physical hardware, reset all the switches to
            # inactive. Note if we have a keymap with active keys, the
            # keyboard interface will reset those switches as needed.
            for switch in self.machine.switches:
                self.switches.update({switch.name: {'state': 0, 'time': 0}})

    def is_state(self, switch_name, state, ticks=0):
        """ Returns True if the switch_name has been in the state for the given
        number of ticks. If ticks is not specific, returns True if the switch
        is in the state regardless of how long its been in that state.
        """

        if self.switches[switch_name]['state'] == state:
            if ticks <= self.ticks_since_change(switch_name):
                return True
            else:
                return False
        else:
            return False

    def is_active(self, switch_name, ticks=None):
        """Returns True if the current switch is active. If optional arg ticks
        is passed, will only return true if switch has been active for that
        many ticks.
        """

        return self.is_state(switch_name=switch_name,
                             state=1,
                             ticks=ticks)

    def is_inactive(self, switch_name, ticks=None):
        """Returns True if the current switch is inactive. If optional arg
        ticks is passed, will only return true if switch has been inactive
        for that many ticks.
        """

        return self.is_state(switch_name=switch_name,
                             state=0,
                             ticks=ticks)

    def ticks_since_change(self, switch_name):
        """Returns the number of ticks that have elapsed since this switch
        last changed state.
        """

        return mpf.timing.tick - self.switches[switch_name]['time']

    def tick(self):
        """ Check the current list of active timed switches to see if its
        time to take action on any of them. If so, do the callback and then
        remove that entry from the list.
        """
        # Make a copy so we can delete from the orig list while iterating.
        active_times_switches_copy = dict(self.active_timed_switches)
        for k, v in active_times_switches_copy.iteritems():
            if k <= mpf.timing.tick:  # change to generator?
                for item in v:
                    item['callback']()
                del self.active_timed_switches[k]

    def set_state(self, switch_name, state):
        self.switches.update({switch_name: {'state': state,
                                            'time': mpf.timing.tick
                                            }
                              })

    def process_switch(self, name=None, state=1, num=None, obj=None):
        """ Processes a new switch state change. Default is to pass in a name,
        but you can also pass a switch based on its number or a reference to
        the switch object.

        State 0 means the switch changed from active to inactive, and 1 means
        it changed from inactive to active. (The hardware & platform code
        handles NC versus NO switches and translates them to 'active' versus
        'inactive'.
        """

        # Find the switch name
        if num is not None:
            for switch in self.machine.switches:
                if switch.number == num:
                    name = switch.name
                    break
        elif obj:
            name = obj.name

        # flip the incoming state if the switch type is NC
        if self.machine.switches[name].type == 'NC':
            state = state ^ 1

        self.log.debug("Processing switch: %s, State:%s", name, state)

        # Update the machine's switch state
        self.set_state(name, state)

        # Combines name & state so we can look it up
        switch_key = str(name) + '-' + str(state)

        # Do we have any registered handlers for this switch/state combo?
        if switch_key in self.registered_switches:
            for entry in self.registered_switches[switch_key]:  # generator?
                # Found an entry.

                if entry['ticks']:
                    # This entry is for a timed switch, so add it to our
                    # active timed switch list
                    key = mpf.timing.tick + entry['ticks']
                    value = {'switch_action': str(name) + '-' + str(state),
                             'callback': entry['callback']}
                    self.active_timed_switches[key].append(value)
                else:
                    # This entry doesn't have a timed delay, so do the action
                    # now
                    entry['callback']()

                # todo need to add args and kwargs support to callback

        # now check if the opposite state is in the active timed switches list
        # if so, remove it
        for k, v, in self.active_timed_switches.items():
            # using items() instead of iteritems() since we might want to
            # delete while iterating

            for item in v:
                if item['switch_action'] == str(name) + '-' + str(state ^ 1):
                    # ^1 in above line invertes the state
                    del self.active_timed_switches[k]

        self.post_switch_events(name, state)

    def add_switch_handler(self, switch_name, callback, state=1, ms=0):
        """ Register a handler to take action on some switch event. These
        events can be trigger when a switch becomes active (state=1) or
        inactive (state=0).

        If you specify a 'ms' parameter, the handler won't be called until the
        switch is in that state for that many ms (rounded up to the nearst
        machine timer tick).

        You can mix & match entries for the same switch here.
        """
        # convert ms into number of machine ticks

        ticks = int(math.ceil((ms/mpf.timing.secs_per_tick/1000)))

        entry_val = {'ticks': ticks, 'callback': callback}
        entry_key = str(switch_name) + '-' + str(state)

        self.registered_switches[entry_key].append(entry_val)

    def post_switch_events(self, switch_name, state):
        """ Posts the game events based on this switch changing state.
        """

        # post events based on the switch tags

        # the following events all fire the moment a switch goes active
        if state == 1:

            for tag in self.machine.switches[switch_name].tags:
                self.machine.events.post("sw_" + tag)

        # the following events all fire the moment a switch becomes inactive
        elif state == 0:
            pass