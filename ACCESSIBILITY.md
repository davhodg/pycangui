# Accessibility

pycangui should be usable by anybody who works on a CAN bus, including people who use a
keyboard and no mouse, a screen reader, large text, or a high contrast theme. This page says what
that means here, what has been checked and what has not, and how to report something that gets
in your way.

It is a statement of what is aimed for and of what is known, not a claim of conformance to a
standard. Nothing here has been evaluated by anybody but the maintainer.

## What is aimed for

- **Keyboard.** Everything can be reached and used without a mouse, the control that has the
  focus can be seen, and nothing traps it.
- **Screen readers.** Every control has a name that says what it does, and a group of controls
  has a name for the group.
- **Text size and scaling.** The window follows the size of text and the display scaling the
  operating system is set to, and stays usable at twice the ordinary size.
- **Colour.** Colour is never the only thing that says something, and what is coloured can still
  be read in a high contrast theme and in both the light and the dark one.
- **The command line and the logs.** `--help` says what each option does, a failure exits with a
  code that is not zero, and a problem is named in words.

## What has been checked

Checked means tried by the maintainer, on Windows 11. Each line says how.

| | State |
| --- | --- |
| High contrast theme | Tried: readable, and nothing was lost. |
| Light and dark themes | In daily use. |
| Display scaling | In daily use at 125 %. Not tried at 200 %. |
| Large text | Lists, entry boxes and title-bar buttons follow the system's text size. Every pane was measured with text at 150 % and fits a 1280 pixel wide screen. Measured from pictures of the window, and not yet tried on a screen at that size. |
| Colour as the only signal | The Event Log begins a problem with *Warning:* or *Error:* as well as colouring it. The other places a colour is used have not been gone through. |
| Keyboard only | Not yet checked. |
| Screen reader | Not yet checked, with any of them. |
| Linux and macOS | pycangui runs and is tested on both. Neither has been checked for any of the above. |

## Known limitations

- The **Pin** and **Detach** buttons on a floating pane cannot be reached with the keyboard.
- The **plot** is a picture, and says nothing to a screen reader. The values it draws are in the
  list beside it, and *Export...* writes them to a file.
- The plot's background is white whatever the theme.
- Some buttons are a symbol and no word -- **+** and **-** beside the channel -- and are named
  only by their tooltip.

If you find another, please report it: this list is as long as what has been looked for.

## Reporting a barrier

Open an issue with the
[accessibility form](https://github.com/davhodg/pycangui/issues/new?template=accessibility.yml).
It asks what you were trying to do, what happened, and what you were using: the operating
system, and the text size, theme or assistive technology where it matters. A screenshot helps
and is not required. Nobody is asked about a disability, and nobody needs to say why they work
the way they do.

A barrier is ranked by how far it stops the work:

- **Critical** -- something cannot be done at all.
- **Serious** -- it can be done, with difficulty or by a way round.
- **Moderate** -- it is harder than it should be.
- **Minor** -- it is a nuisance.

## What happens to a report

pycangui is looked after by one person in their own time, so no date is promised. What is
promised is that every report is read and answered, that the answer says what is known and what
will be done, that a way round is given where there is one, and that you are asked whether a fix
works before the issue is closed. A report of a barrier is taken as something learned about the
program, not as a complaint.

## For contributors

A change to what is on screen keeps to these, and the pull request checklist asks about them:

- **No font and no size in pixels written into a pane.** The fixed-pitch font and the width of a
  box come from `pycangui/ui/fonts.py`, which follows the system's text size.
- **Colour is not the only signal.** What a colour says is also said by a word, a mark or a
  tooltip.
- **A new control can be reached with the keyboard and has a name.** A button that is a symbol
  has an accessible name as well as a tooltip.
- **What is pressed often is a button; what is done once is in a menu.** A row of buttons wider
  than the pane is the commonest way a pane stops fitting at large text.

Tried with the text size turned up, and with the keyboard alone, before it is sent.

## Who looks after this

The maintainer, [@davhodg](https://github.com/davhodg), who answers the reports and keeps this
page in step with the program. Suggestions for the page itself are welcome as an issue or a pull
request.
