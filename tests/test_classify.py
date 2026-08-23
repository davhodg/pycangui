from pycangui.core.classify import classify, group_of


def test_predefined_connection_set():
    assert classify(0x000, False) == ("NMT", "NMT")
    assert classify(0x080, False) == ("SYNC", "SYNC/TIME")
    assert classify(0x100, False) == ("TIME", "SYNC/TIME")
    assert classify(0x085, False) == ("EMCY n5", "EMCY")
    assert classify(0x185, False) == ("TxPDO1 n5", "PDO")
    assert classify(0x205, False) == ("RxPDO1 n5", "PDO")
    assert classify(0x485, False) == ("TxPDO4 n5", "PDO")
    assert classify(0x585, False) == ("SDO-T n5", "SDO")
    assert classify(0x605, False) == ("SDO-R n5", "SDO")
    assert classify(0x705, False) == ("Heartbeat n5", "Heartbeat")
    assert classify(0x77F, False) == ("Heartbeat n127", "Heartbeat")
    assert classify(0x7E5, False) == ("LSS", "LSS")


def test_unknown_ids_are_other():
    assert classify(0x123, False) == ("", "Other")  # 0x100 block with node 35 -> TIME? no: fn 2
    assert classify(0x180, False) == ("", "Other")  # node 0 is not a node
    assert classify(0x7FF, False) == ("", "Other")
    assert classify(0x18DA00F1, True) == ("", "J1939")  # 29-bit: J1939 group, label from manager


def test_group_of_user_labels():
    assert group_of("TxPDO1 n5") == "PDO"
    assert group_of("RxPDO2 n9") == "PDO"
    assert group_of("Pump status") == "Other"
    assert group_of("sdo something") == "SDO"
    assert group_of("Heartbeat n3") == "Heartbeat"
    assert group_of("") == "Other"
