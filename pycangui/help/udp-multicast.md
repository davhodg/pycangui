[&larr; Contents](manual.md)

# UDP multicast

pycangui uses UDP multicast for two things, with no adapter and nothing else to
install:

- **udp_multicast channels**, which share CAN frames with other programs built on
  python-can -- a script of your own, a simulator, a test rig, another tool -- and
  receive the CAN [pyscopegui](https://github.com/davhodg/pyscopegui) decodes
  from a scope or logic analyser;
- **sending signals to pyscopegui**, to show beside its instruments and to
  trigger on (see [Sending signals to pyscopegui](signals.md)).

A multicast group is a network address that every program which has joined it
hears, and can send to.

## How far it goes

**Tools > Settings > UDP multicast** decides, for both, in both directions:

| Choice | Channels | Signals to pyscopegui |
|---|---|---|
| **Off** | Do not connect; one connected is disconnected | Not sent; *Send ticked signals to pyscopegui* is greyed |
| **This computer only** (the first time) | Frames go to programs on this computer only, and frames from other computers are dropped | Sent to programs on this computer only |
| **Local network** | Frames reach the other computers on the same network segment, and theirs are received | Sent to the same network segment |

- **This computer only** is enforced both ways. What pycangui sends goes with a
  hop limit of 0, which no network adapter passes on. python-can listens on every
  network adapter, so a frame another computer sends to the group can still
  arrive: pycangui drops it, and the Event Log names each computer that tried, once.
- **Local network** goes no further than the network segment: the hop limit is 1,
  so no router passes anything on. Every computer on that segment that has joined
  a group hears pycangui -- and can send frames into a udp_multicast channel, and
  so into pycangui, where they are decoded, recorded and answered like frames from
  an adapter. Use it on a network you trust.
- It is a setting for the computer, not the workspace. A channel already
  connected is connected again when it changes, so it follows at once; the
  signals follow at once too.
- *Send ticked signals to pyscopegui* is below the three choices in the same
  menu. While UDP multicast is off it is greyed and nothing is sent; choosing
  *This computer only* or *Local network* again puts it back as it was.

Across computers, *Local network* also needs:

- a firewall that lets UDP in, for Python, on each receiving computer: port 43113
  for channels, 43114 for the signals;
- a network that carries multicast: a switch usually does, Wi-Fi often badly,
  and some company networks not at all;
- on a computer with more than one network adapter, the system chooses which one
  a group is joined on, and python-can offers no way to choose another.

Everything goes as UDP, which does not resend what is lost, so on a busy or poor
network some frames may not arrive, and none says so. For watching and
simulating that is usually fine; it is no way to control equipment.

## Sharing frames with other programs

- **Every program must use the same group address and the same port.** A
  program on a different group, or the same group on another port, hears
  nothing and is heard by nobody, without any error.
- In pycangui, add a channel, choose the interface **udp_multicast**, and pick
  the group from the **Port** list. It offers python-can's two defaults:
  `ff15:7079:7468:6f6e:6465:6d6f:6d63:6173`, the IPv6 group a python-can
  program joins when it names none, and `239.74.163.2`, python-can's IPv4 one.
  It also offers `239.74.163.10 (pyscopegui)`, the group pyscopegui sends the
  CAN it decodes from a scope or logic analyser to: a group of its own, so that
  those frames are a channel of their own and not mixed with anything else.
  The box is editable: type any other group a program uses. The UDP port is
  python-can's default, 43113. *Bitrate* is greyed: there is no wire to time.
- In the other program, open a bus on the same group. Give it the same reach as
  pycangui: python-can's own default is the local network, so a program left at
  it sends its frames out on the network even while pycangui keeps to this
  computer.

  ```python
  import can

  # hop_limit=0 keeps it on this computer, as pycangui does unless told otherwise
  bus = can.Bus(interface="udp_multicast", hop_limit=0)  # python-can's default group
  # or, to match pycangui's other choice:
  # bus = can.Bus(interface="udp_multicast", channel="239.74.163.2", hop_limit=0)
  bus.send(can.Message(arbitration_id=0x123, data=[1, 2, 3], is_extended_id=False))
  ```

- Either side can be started first, and there can be more than two: each
  program joins the group and hears every frame the others send. Neither owns
  the bus. Two separate sets of programs can share frames without hearing each
  other by using different groups.
- The other program should leave the port at python-can's default: pycangui
  does not offer another.
- What pycangui sends is heard by the others, and shown in its own trace as
  sent; what they send is shown as received. CAN FD frames are carried too.
- On the pyscopegui group the frames come from a scope or logic analyser, which
  only watches the wire: nothing sent on that channel reaches the bus it is
  watching.
- python-can needs the `msgpack` package for this interface, which pycangui
  installs with itself on Windows; elsewhere python-can installs it.
- It is a channel like the others, so a [simulated node](virtual.md) can be
  run on it, and a protocol pane can work with a device another program
  simulates.
