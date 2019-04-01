# The MIT License (MIT)
#
# Copyright (c) 2019 Kevin J. Walters
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
"""
`adafruit_midi`
================================================================================

A CircuitPython helper for encoding/decoding MIDI packets over a MIDI or UART connection.


* Author(s): Kevin J. Walters

Implementation Notes
--------------------

**Hardware:**



**Software and Dependencies:**

* Adafruit CircuitPython firmware for the supported boards:
  https://github.com/adafruit/circuitpython/releases

"""

__version__ = "0.0.0-auto.0"
__repo__ = "https://github.com/adafruit/Adafruit_CircuitPython_MIDI.git"

# This is a special channel value outside of wire protocol range used to
# represent all of the sixteen channels
ALL_CHANNELS = -1

# From C3
# Semitones    A   B   C   D   E   F   G
NOTE_OFFSET = [9, 11, 12, 14, 16, 17, 19]

# pylint: disable=no-else-return
def channel_filter(channel, channel_spec):
    """
    Utility function to return True iff the given channel matches channel_spec.
    """
    if isinstance(channel_spec, int):
        if channel_spec == ALL_CHANNELS:
            return True
        else:
            return channel == channel_spec
    elif isinstance(channel_spec, tuple):
        return channel in channel_spec
    else:
        raise ValueError("Incorrect type for channel_spec")


def note_parser(note):
    """If note is a string then it will be parsed and converted to a MIDI note (key) number, e.g.
    "C4" will return 60, "C#4" will return 61. If note is not a string it will simply be returned.

    :param note: Either 0-127 int or a str representing the note, e.g. "C#4"
    """
    midi_note = note
    if isinstance(note, str):
        if len(note) < 2:
            raise ValueError("Bad note format")
        noteidx = ord(note[0].upper()) - 65  # 65 os ord('A')
        if not 0 <= noteidx <= 6:
            raise ValueError("Bad note")
        sharpen = 0
        if note[1] == '#':
            sharpen = 1
        elif note[1] == 'b':
            sharpen = -1
        # int may throw exception here
        midi_note = (int(note[1 + abs(sharpen):]) * 12
                     + NOTE_OFFSET[noteidx]
                     + sharpen)

    return midi_note


class MIDIMessage:
    """
    A MIDI message:
      - _STATUS - extracted from Status byte with channel replaced by 0s
                  (high bit always set).
      - _STATUSMASK - mask used to compared a status byte with _STATUS value
      - LENGTH - length for a fixed size message including status
                  or -1 for variable length.
      - CHANNELMASK - mask use to apply a (wire protocol) channel number.
      - ENDSTATUS - the EOM status byte, only set for variable length.

      This is an abstract class.
    """
    _STATUS = None
    _STATUSMASK = None
    LENGTH = None
    CHANNELMASK = None
    ENDSTATUS = None

    # Commonly used exceptions to save memory
    _EX_VALUEERROR_OOR = ValueError("Out of range")

    # Each element is ((status, mask), class)
    # order is more specific masks first
    _statusandmask_to_class = []

    @classmethod
    def register_message_type(cls):
        """Register a new message by its status value and mask.
        """
        ### These must be inserted with more specific masks first
        insert_idx = len(MIDIMessage._statusandmask_to_class)
        for idx, m_type in enumerate(MIDIMessage._statusandmask_to_class):
            if cls._STATUSMASK > m_type[0][1]:
                insert_idx = idx
                break

        MIDIMessage._statusandmask_to_class.insert(insert_idx,
                                                   ((cls._STATUS, cls._STATUSMASK), cls))


    # pylint: disable=too-many-arguments
    @classmethod
    def _search_eom_status(cls, buf, eom_status, msgstartidx, msgendidxplusone, endidx):
        good_termination = False
        bad_termination = False

        msgendidxplusone = msgstartidx + 1
        while msgendidxplusone <= endidx:
            # Look for a status byte
            # Second rule of the MIDI club is status bytes have MSB set
            if buf[msgendidxplusone] & 0x80:
                # pylint disable=simplifiable-if-statement
                if buf[msgendidxplusone] == eom_status:
                    good_termination = True
                else:
                    bad_termination = True
                break
            else:
                msgendidxplusone += 1

        if good_termination or bad_termination:
            msgendidxplusone += 1

        return (msgendidxplusone, good_termination, bad_termination)

    # pylint: disable=too-many-arguments,too-many-locals
    @classmethod
    def _match_message_status(cls, buf, channel_in, msgstartidx, msgendidxplusone, endidx):
        msgclass = None
        status = buf[msgstartidx]
        known_msg = False
        complete_msg = False
        bad_termination = False
        channel_match_orna = True
        channel = None

        # Rummage through our list looking for a status match
        for status_mask, msgclass in MIDIMessage._statusandmask_to_class:
            masked_status = status & status_mask[1]
            if status_mask[0] == masked_status:
                known_msg = True
                # Check there's enough left to parse a complete message
                # this value can be changed later for a var. length msgs
                complete_msg = len(buf) - msgstartidx >= msgclass.LENGTH
                if not complete_msg:
                    break

                if msgclass.CHANNELMASK is not None:
                    channel = status & msgclass.CHANNELMASK
                    channel_match_orna = channel_filter(channel, channel_in)

                if msgclass.LENGTH < 0:  # indicator of variable length message
                    (msgendidxplusone,
                     terminated_msg,
                     bad_termination) = cls._search_eom_status(buf,
                                                               msgclass.ENDSTATUS,
                                                               msgstartidx,
                                                               msgendidxplusone,
                                                               endidx)
                    if not terminated_msg:
                        complete_msg = False
                else: # fixed length message
                    msgendidxplusone = msgstartidx + msgclass.LENGTH
                break

        return (msgclass, status,
                known_msg, complete_msg, bad_termination,
                channel_match_orna, channel, msgendidxplusone)

    @classmethod
    def from_message_bytes(cls, midibytes, channel_in):
        """Create an appropriate object of the correct class for the
        first message found in some MIDI bytes.

        Returns (messageobject, endplusone, skipped, channel)
        or for no messages, partial messages or messages for other channels
        (None, endplusone, skipped, None).
        """
        msg = None
        endidx = len(midibytes) - 1
        skipped = 0
        preamble = True
        channel = None

        msgstartidx = 0
        msgendidxplusone = 0
        while True:
            # Look for a status byte
            # Second rule of the MIDI club is status bytes have MSB set
            while msgstartidx <= endidx and not midibytes[msgstartidx] & 0x80:
                msgstartidx += 1
                if preamble:
                    skipped += 1
            preamble = False

            # Either no message or a partial one
            if msgstartidx > endidx:
                return (None, endidx + 1, skipped, None)

            # Try and match the status byte found in midibytes
            (msgclass,
             status,
             known_message,
             complete_message,
             bad_termination,
             channel_match_orna,
             channel,
             msgendidxplusone) = cls._match_message_status(midibytes,
                                                           channel_in,
                                                           msgstartidx,
                                                           msgendidxplusone,
                                                           endidx)

            if complete_message and not bad_termination and channel_match_orna:
                try:
                    msg = msgclass.from_bytes(midibytes[msgstartidx+1:msgendidxplusone])
                except(ValueError, TypeError) as ex:
                    msg = MIDIBadEvent(midibytes[msgstartidx+1:msgendidxplusone], ex)

            # break out of while loop for a complete message on good channel
            # or we have one we do not know about
            if known_message:
                if complete_message:
                    if channel_match_orna:
                        break
                    else:  # advance to next message
                        msgstartidx = msgendidxplusone
                else:
                    # Important case of a known message but one that is not
                    # yet complete - leave bytes in buffer and wait for more
                    break
            else:
                msg = MIDIUnknownEvent(status)
                # length cannot be known
                # next read will skip past leftover data bytes
                msgendidxplusone = msgstartidx + 1
                break

        return (msg, msgendidxplusone, skipped, channel)

    # channel value present to keep interface uniform but unused
    # pylint: disable=unused-argument
    def as_bytes(self, channel=None):
        """A default method for constructing wire messages with no data.
        Returns a (mutable) bytearray with just status code in."""
        return bytearray([self._STATUS])

    # databytes value present to keep interface uniform but unused
    # pylint: disable=unused-argument
    @classmethod
    def from_bytes(cls, databytes):
        """A default method for constructing message objects with no data.
           Returns the new object."""
        return cls()


# DO NOT try to register these messages
class MIDIUnknownEvent(MIDIMessage):
    """An unknown MIDI message.

    :param int status: The MIDI status number.

    This can either occur because there is no class representing the message
    or because it is not imported.
    """
    LENGTH = -1

    def __init__(self, status):
        self.status = status


class MIDIBadEvent(MIDIMessage):
    """A bad MIDI message, one that could not be parsed/constructed.

    :param list data: The MIDI status number.
    :param Exception exception: The exception used to store the repr() text representation.

    This could be due to status bytes appearing where data bytes are expected.
    """
    LENGTH = -1

    def __init__(self, data, exception):
        self.data = bytearray(data)
        self.exception_text = repr(exception)
