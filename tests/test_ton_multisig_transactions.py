import base64
import unittest

from tonsdk.boc import Cell, DictBuilder

from ton_multisig_transactions import (
    AUTO_ORDER_SEQNO,
    OP_NEW_ORDER,
    OP_ORDER_APPROVE,
    build_new_order_payload,
    build_order_approve_payload,
)


class MultisigTransactionBodyTests(unittest.TestCase):
    def _valid_action_boc(self):
        # TEP-62 NFT transfer body with a standard workchain-0 new owner.
        body = Cell()
        body.bits.write_uint(0x5FCC3D14, 32)
        body.bits.write_uint(7, 64)
        body.bits.write_address(__import__("tonsdk").utils.Address("0:" + "11" * 32))
        body.bits.write_address(None)
        body.bits.write_bit(0)  # no custom payload
        body.bits.write_coins(1)
        body.bits.write_bit(0)  # inline forward payload
        message = Cell()
        message.bits.write_bit(0)  # internal message
        message.bits.write_bit(1)  # ihr_disabled
        message.bits.write_bit(1)  # bounce
        message.bits.write_bit(0)  # not bounced
        message.bits.write_address(None)
        message.bits.write_address(__import__("tonsdk").utils.Address("0:" + "22" * 32))
        message.bits.write_coins(100_000_000)
        message.bits.write_bit(0)  # no extra currencies
        message.bits.write_coins(0)
        message.bits.write_coins(0)
        message.bits.write_uint(0, 64)
        message.bits.write_uint(0, 32)
        message.bits.write_bit(0)  # no state init
        message.bits.write_bit(1)  # body by ref
        message.refs.append(body)
        action = Cell()
        action.bits.write_uint(0xF1381E5B, 32)
        action.bits.write_uint(3, 8)
        action.refs.append(message)
        actions = DictBuilder(8).store_ref(0, action).end_cell()
        return base64.b64encode(actions.to_boc(False)).decode("ascii")

    def test_builds_new_order_body_using_official_opcode_and_auto_seqno(self):
        result = build_new_order_payload(
            self._valid_action_boc(),
            signer_index=1,
            signer_addresses=["0:" + "01" * 32, "0:" + "02" * 32, "0:" + "03" * 32],
            expiration_date=1700003600,
            now=1700000000,
        )
        body = Cell.one_from_boc(base64.b64decode(result["payload"]))
        sl = body.begin_parse()
        self.assertEqual(sl.read_uint(32), OP_NEW_ORDER)
        sl.read_uint(64)
        self.assertEqual(sl.read_uint(256), AUTO_ORDER_SEQNO)
        self.assertEqual(sl.read_bit(), 1)
        self.assertEqual(sl.read_uint(8), 1)
        self.assertEqual(sl.read_uint(48), 1700003600)
        self.assertEqual(len(body.refs), 1)
        self.assertEqual(result["action_count"], 1)
        self.assertFalse(result["approval_safe"])

    def test_rejects_non_signer_index(self):
        with self.assertRaises(ValueError):
            build_new_order_payload(
                self._valid_action_boc(),
                signer_index=3,
                signer_addresses=["a", "b", "c"],
                expiration_date=1700003600,
                now=1700000000,
            )

    def test_rejects_unknown_or_governance_actions(self):
        action = Cell()
        action.bits.write_uint(0xDEADBEEF, 32)
        actions = DictBuilder(8).store_ref(0, action).end_cell()
        with self.assertRaises(ValueError):
            build_new_order_payload(
                base64.b64encode(actions.to_boc(False)).decode("ascii"),
                signer_index=0,
                signer_addresses=["a", "b", "c"],
                expiration_date=1700003600,
                now=1700000000,
            )

    def test_builds_approval_body_with_official_opcode(self):
        result = build_order_approve_payload(signer_index=2)
        body = Cell.one_from_boc(base64.b64decode(result["payload"]))
        sl = body.begin_parse()
        self.assertEqual(sl.read_uint(32), OP_ORDER_APPROVE)
        sl.read_uint(64)
        self.assertEqual(sl.read_uint(8), 2)
        self.assertTrue(sl.is_empty())


if __name__ == "__main__":
    unittest.main()
