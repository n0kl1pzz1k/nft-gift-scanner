import "@acton/emulation/network"
import "@acton/emulation/testing"
import "@acton/testing/expect"
import "@contracts/escrow-types"
import "@wrappers/GiftRadarEscrow.gen"

fun setupEscrow(): (GiftRadarEscrow, address, address, address, address, address, address) {
    val seller = testing.treasury("seller");
    val buyer = testing.treasury("buyer");
    val nftItem = testing.treasury("nft-item");
    val authority = testing.treasury("payment-authority");
    val arb1 = testing.treasury("arbitrator-one");
    val arb2 = testing.treasury("arbitrator-two");

    val escrow = GiftRadarEscrow.fromStorage({
        dealId: 77,
        expectedAmount: 250,
        deadline: testing.getNow() + 86400,
        initialized: false,
        state: 0,
        preDisputeState: 0,
        disputeApprovals: 0,
        resolutionDecision: 0,
        disputeReasonHash: 0,
        transferQueryId: 0,
        parties: EscrowParties {
            seller: seller.address,
            buyer: buyer.address,
            nftItem: nftItem.address,
        }.toCell(),
        authorities: EscrowAuthorities {
            paymentAuthority: authority.address,
            arbitrator1: arb1.address,
            arbitrator2: arb2.address,
        }.toCell(),
        paymentEvidence: PaymentEvidence {
            paymentReferenceHash: 0,
            refundReferenceHash: 0,
            settlementReferenceHash: 0,
        }.toCell(),
        deliveryEvidence: DeliveryEvidence {
            buyerOwnershipProofHash: 0,
            sellerOwnershipProofHash: 0,
            resolutionHash: 0,
        }.toCell(),
    });
    val deployed = escrow.deploy(authority.address, { value: grams("1") });
    expect(deployed).toHaveSuccessfulDeploy({ to: escrow.address });
    val initialized = net.send(authority.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: DeployEscrow { queryId: 0 },
    }));
    expect(initialized).toHaveSuccessfulTx<DeployEscrow>({
        from: authority.address,
        to: escrow.address,
    });
    return (escrow, seller.address, buyer.address, nftItem.address, authority.address, arb1.address, arb2.address);
}

fun depositNft(escrow: GiftRadarEscrow, seller: address, nftItem: address) {
    val result = net.send(nftItem, createMessage({
        bounce: true,
        value: grams("0.2"),
        dest: escrow.address,
        body: NotificationForNewOwner {
            queryId: 1,
            oldOwnerAddress: seller,
            payload: createEmptySlice(),
        },
    }));
    expect(result).toHaveSuccessfulTx<NotificationForNewOwner>({
        from: nftItem,
        to: escrow.address,
    });
}

fun readEscrowState(escrow: GiftRadarEscrow): EscrowStateReply {
    return net.runGetMethod<EscrowStateReply>(
        escrow.address,
        "get_escrow_state",
    );
}

get fun `test only configured NFT item and seller can deposit`() {
    val (escrow, seller, _, nftItem, _, _, _) = setupEscrow();
    val outsider = testing.treasury("fake-nft-item");
    val bad = net.send(outsider.address, createMessage({
        bounce: true,
        value: grams("0.2"),
        dest: escrow.address,
        body: NotificationForNewOwner {
            queryId: 1,
            oldOwnerAddress: seller,
            payload: createEmptySlice(),
        },
    }));
    expect(bad).toHaveFailedTx<NotificationForNewOwner>({
        from: outsider.address,
        to: escrow.address,
        exitCode: 1003,
    });
    expect(readEscrowState(escrow).state).toEqual(0);

    depositNft(escrow, seller, nftItem);
    expect(readEscrowState(escrow).state).toEqual(1);
}

get fun `test deposit rejects a previous owner other than seller`() {
    val (escrow, _, _, nftItem, _, _, _) = setupEscrow();
    val impostor = testing.treasury("impostor-seller");
    val bad = net.send(nftItem, createMessage({
        bounce: true,
        value: grams("0.2"),
        dest: escrow.address,
        body: NotificationForNewOwner {
            queryId: 2,
            oldOwnerAddress: impostor.address,
            payload: createEmptySlice(),
        },
    }));
    expect(bad).toHaveFailedTx<NotificationForNewOwner>({
        from: nftItem,
        to: escrow.address,
        exitCode: 1001,
    });
    expect(readEscrowState(escrow).state).toEqual(0);
}

get fun `test payment attestation is authority-only and exact-amount`() {
    val (escrow, seller, _, nftItem, authority, _, _) = setupEscrow();
    depositNft(escrow, seller, nftItem);

    val outsider = testing.treasury("payment-outsider");
    val unauthorized = net.send(outsider.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RecordStarsPayment {
            queryId: 1,
            amount: 250,
            telegramChargeHash: 12345,
        },
    }));
    expect(unauthorized).toHaveFailedTx<RecordStarsPayment>({
        from: outsider.address,
        to: escrow.address,
        exitCode: 1001,
    });

    val wrongAmount = net.send(authority, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RecordStarsPayment {
            queryId: 2,
            amount: 249,
            telegramChargeHash: 12346,
        },
    }));
    expect(wrongAmount).toHaveFailedTx<RecordStarsPayment>({
        from: authority,
        to: escrow.address,
        exitCode: 1004,
    });
    expect(readEscrowState(escrow).state).toEqual(1);
}

get fun `test duplicate Telegram payment attestation is idempotent`() {
    val (escrow, seller, _, nftItem, authority, _, _) = setupEscrow();
    depositNft(escrow, seller, nftItem);

    val payment = RecordStarsPayment {
        queryId: 3,
        amount: 250,
        telegramChargeHash: 0xabc123,
    };
    val first = net.send(authority, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: payment,
    }));
    expect(first).toHaveSuccessfulTx<RecordStarsPayment>({
        from: authority,
        to: escrow.address,
    });

    val duplicate = net.send(authority, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: payment,
    }));
    expect(duplicate).toHaveSuccessfulTx<RecordStarsPayment>({
        from: authority,
        to: escrow.address,
    });
    expect(readEscrowState(escrow).state).toEqual(2);
}

get fun `test seller payout cannot be marked before buyer delivery`() {
    val (escrow, seller, _, nftItem, authority, _, _) = setupEscrow();
    depositNft(escrow, seller, nftItem);
    net.send(authority, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RecordStarsPayment {
            queryId: 4,
            amount: 250,
            telegramChargeHash: 0xabc124,
        },
    }));

    val early = net.send(authority, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: MarkSellerSettled {
            queryId: 5,
            payoutReferenceHash: 0xabc125,
        },
    }));
    expect(early).toHaveFailedTx<MarkSellerSettled>({
        from: authority,
        to: escrow.address,
        exitCode: 1002,
    });
    expect(readEscrowState(escrow).state).toEqual(2);
}

get fun `test release is buyer-only and moves to transfer pending`() {
    val (escrow, seller, buyer, nftItem, authority, _, _) = setupEscrow();
    depositNft(escrow, seller, nftItem);
    net.send(authority, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RecordStarsPayment {
            queryId: 6,
            amount: 250,
            telegramChargeHash: 0xabc126,
        },
    }));

    val outsider = testing.treasury("release-outsider");
    val bad = net.send(outsider.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RequestNftRelease { queryId: 7 },
    }));
    expect(bad).toHaveFailedTx<RequestNftRelease>({
        from: outsider.address,
        to: escrow.address,
        exitCode: 1001,
    });
    expect(readEscrowState(escrow).state).toEqual(2);

    val release = net.send(buyer, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RequestNftRelease { queryId: 8 },
    }));
    expect(release).toHaveSuccessfulTx<RequestNftRelease>({
        from: buyer,
        to: escrow.address,
    });
    expect(release).toHaveTx<AskToChangeOwnership>({
        from: escrow.address,
        to: nftItem,
    });
    expect(readEscrowState(escrow).state).toEqual(3);
}

get fun `test dispute dismissal requires two distinct arbitrators`() {
    val (escrow, seller, _, nftItem, authority, arb1, arb2) = setupEscrow();
    depositNft(escrow, seller, nftItem);
    net.send(authority, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RecordStarsPayment {
            queryId: 9,
            amount: 250,
            telegramChargeHash: 0xabc127,
        },
    }));

    val dispute = net.send(seller, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RaiseDispute { queryId: 10, reasonHash: 0xdef001 },
    }));
    expect(dispute).toHaveSuccessfulTx<RaiseDispute>({
        from: seller,
        to: escrow.address,
    });
    expect(readEscrowState(escrow).state).toEqual(7);

    val first = net.send(arb1, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ApproveDisputeResolution {
            queryId: 11,
            decision: 2,
            resolutionHash: 0xdef002,
        },
    }));
    expect(first).toHaveSuccessfulTx<ApproveDisputeResolution>({
        from: arb1,
        to: escrow.address,
    });
    expect(readEscrowState(escrow).state).toEqual(7);

    val second = net.send(arb2, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ApproveDisputeResolution {
            queryId: 12,
            decision: 2,
            resolutionHash: 0xdef002,
        },
    }));
    expect(second).toHaveSuccessfulTx<ApproveDisputeResolution>({
        from: arb2,
        to: escrow.address,
    });
    expect(readEscrowState(escrow).state).toEqual(2);
    expect(readEscrowState(escrow).disputeApprovals).toEqual(3);
}

get fun `test dispute during pending NFT transfer is quarantined`() {
    val (escrow, seller, buyer, nftItem, authority, _, _) = setupEscrow();
    depositNft(escrow, seller, nftItem);
    net.send(authority, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RecordStarsPayment {
            queryId: 13,
            amount: 250,
            telegramChargeHash: 0xabc128,
        },
    }));
    net.send(buyer, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RequestNftRelease { queryId: 14 },
    }));

    val dispute = net.send(buyer, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RaiseDispute { queryId: 15, reasonHash: 0xdef003 },
    }));
    expect(dispute).toHaveSuccessfulTx<RaiseDispute>({
        from: buyer,
        to: escrow.address,
    });
    expect(readEscrowState(escrow).state).toEqual(8);
}

get fun `test delivery proof is required before seller settlement`() {
    val (escrow, seller, buyer, nftItem, authority, _, _) = setupEscrow();
    depositNft(escrow, seller, nftItem);
    net.send(authority, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RecordStarsPayment {
            queryId: 16,
            amount: 250,
            telegramChargeHash: 0xabc129,
        },
    }));
    net.send(buyer, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RequestNftRelease { queryId: 17 },
    }));

    val delivered = net.send(authority, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ConfirmNftDelivery {
            queryId: 18,
            buyerOwnershipProofHash: 0xdef004,
        },
    }));
    expect(delivered).toHaveSuccessfulTx<ConfirmNftDelivery>({
        from: authority,
        to: escrow.address,
    });
    expect(readEscrowState(escrow).state).toEqual(4);

    val settled = net.send(authority, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: MarkSellerSettled {
            queryId: 19,
            payoutReferenceHash: 0xdef005,
        },
    }));
    expect(settled).toHaveSuccessfulTx<MarkSellerSettled>({
        from: authority,
        to: escrow.address,
    });
    expect(readEscrowState(escrow).state).toEqual(5);
}

get fun `test paid deal refund requires Telegram refund attestation and NFT return proof`() {
    val (escrow, seller, _, nftItem, authority, _, _) = setupEscrow();
    depositNft(escrow, seller, nftItem);
    net.send(authority, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RecordStarsPayment {
            queryId: 20,
            amount: 250,
            telegramChargeHash: 0xabc130,
        },
    }));

    val refund = net.send(authority, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RequestRefund {
            queryId: 21,
            telegramRefundHash: 0xdef006,
        },
    }));
    expect(refund).toHaveSuccessfulTx<RequestRefund>({
        from: authority,
        to: escrow.address,
    });
    expect(refund).toHaveTx<AskToChangeOwnership>({
        from: escrow.address,
        to: nftItem,
    });
    expect(readEscrowState(escrow).state).toEqual(9);

    val returned = net.send(authority, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ConfirmNftReturned {
            queryId: 22,
            sellerOwnershipProofHash: 0xdef007,
        },
    }));
    expect(returned).toHaveSuccessfulTx<ConfirmNftReturned>({
        from: authority,
        to: escrow.address,
    });
    expect(readEscrowState(escrow).state).toEqual(6);
}

get fun `test disputed paid deal needs refund attestation and two arbitrators`() {
    val (escrow, seller, buyer, nftItem, authority, arb1, arb2) = setupEscrow();
    depositNft(escrow, seller, nftItem);
    net.send(authority, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RecordStarsPayment {
            queryId: 23,
            amount: 250,
            telegramChargeHash: 0xabc131,
        },
    }));
    net.send(buyer, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RaiseDispute { queryId: 24, reasonHash: 0xdef008 },
    }));

    val firstWithoutRefund = net.send(arb1, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ApproveDisputeResolution {
            queryId: 25,
            decision: 1,
            resolutionHash: 0xdef009,
        },
    }));
    expect(firstWithoutRefund).toHaveSuccessfulTx<ApproveDisputeResolution>({
        from: arb1,
        to: escrow.address,
    });
    val secondWithoutRefund = net.send(arb2, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ApproveDisputeResolution {
            queryId: 26,
            decision: 1,
            resolutionHash: 0xdef009,
        },
    }));
    expect(secondWithoutRefund).toHaveFailedTx<ApproveDisputeResolution>({
        from: arb2,
        to: escrow.address,
        exitCode: 1006,
    });
    expect(readEscrowState(escrow).state).toEqual(7);

    val refundRecorded = net.send(authority, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RecordStarsRefund {
            queryId: 27,
            telegramRefundHash: 0xdef010,
        },
    }));
    expect(refundRecorded).toHaveSuccessfulTx<RecordStarsRefund>({
        from: authority,
        to: escrow.address,
    });

    val secondApproval = net.send(arb2, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: ApproveDisputeResolution {
            queryId: 28,
            decision: 1,
            resolutionHash: 0xdef009,
        },
    }));
    expect(secondApproval).toHaveSuccessfulTx<ApproveDisputeResolution>({
        from: arb2,
        to: escrow.address,
    });
    expect(readEscrowState(escrow).state).toEqual(9);
    expect(secondApproval).toHaveTx<AskToChangeOwnership>({
        from: escrow.address,
        to: nftItem,
    });
}
