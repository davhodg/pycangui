# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A PDO's name says what it is, wherever its COB-ID has been moved to."""

import canopen

from pycangui import resources
from pycangui.canopen.manager import pdo_name


def demo_node(node_id: int = 5):
    return canopen.Network().add_node(node_id, str(resources.path("demo.eds")))


def test_a_tpdo_on_the_default_cob_id_is_named_as_canopen_names_it():
    node = demo_node()
    node.tpdo[1].cob_id = 0x185
    assert pdo_name(node.tpdo[1]) == "TxPDO1_node5" == node.tpdo[1].name


def test_a_tpdo_moved_to_another_cob_id_is_still_a_tpdo_of_its_node():
    """Reported: node 1's TPDO5 on 0x123 was RxPDO0_node35 in Signals and
    Plot, worked out backwards from the COB-ID as though it were the default."""
    node = demo_node(1)
    node.tpdo[1].cob_id = 0x123
    assert pdo_name(node.tpdo[1]) == "TxPDO1_node1"


def test_an_rpdo_moved_is_still_an_rpdo_of_its_node():
    node = demo_node()
    node.rpdo[1].cob_id = 0x1A3  # has the bit a TPDO's default COB-ID has
    assert pdo_name(node.rpdo[1]) == "RxPDO1_node5"
