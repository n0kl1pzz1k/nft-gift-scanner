import "@acton/testing/expect"
import "@wrappers/GiftRadarEscrow.gen"

fun setupEscrow(): (GiftRadarEscrow, Treasury, Treasury, Treasury, Treasury, Treasury, Treasury) {
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
    return (escrow, seller, buyer, nftItem, authority, arb1, arb2);
}

fun depositNft(escrow: GiftRadarEscrow, seller: Treasury, nftItem: Treasury) {
    val result = net.send(nftItem.address, createMessage({
        bounce: true,
        value: grams("0.2"),
        dest: escrow.address,
        body: NotificationForNewOwner {
            queryId: 1,
            oldOwnerAddress: seller.address,
            payload: createEmptySlice(),
        },
    }));
    expect(result).toHaveSuccessfulTx<NotificationForNewOwner>({
        from: nftItem.address,
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
            oldOwnerAddress: seller.address,
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
    val bad = net.send(nftItem.address, createMessage({
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
        from: nftItem.address,
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

    val wrongAmount = net.send(authority.address, createMessage({
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
        from: authority.address,
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
    val first = net.send(authority.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: payment,
    }));
    expect(first).toHaveSuccessfulTx<RecordStarsPayment>({
        from: authority.address,
        to: escrow.address,
    });

    val duplicate = net.send(authority.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: payment,
    }));
    expect(duplicate).toHaveSuccessfulTx<RecordStarsPayment>({
        from: authority.address,
        to: escrow.address,
    });
    expect(readEscrowState(escrow).state).toEqual(2);
}

get fun `test seller payout cannot be marked before buyer delivery`() {
    val (escrow, seller, _, nftItem, authority, _, _) = setupEscrow();
    depositNft(escrow, seller, nftItem);
    net.send(authority.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RecordStarsPayment {
            queryId: 4,
            amount: 250,
            telegramChargeHash: 0xabc124,
        },
    }));

    val early = net.send(authority.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: MarkSellerSettled {
            queryId: 5,
            payoutReferenceHash: 0xabc125,
        },
    }));
    expect(early).toHaveFailedTx<MarkSellerSettled>({
        from: authority.address,
        to: escrow.address,
        exitCode: 1002,
    });
    expect(readEscrowState(escrow).state).toEqual(2);
}

get fun `test release is buyer-only and moves to transfer pending`() {
    val (escrow, seller, buyer, nftItem, authority, _, _) = setupEscrow();
    depositNft(escrow, seller, nftItem);
    net.send(authority.address, createMessage({
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

    val release = net.send(buyer.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RequestNftRelease { queryId: 8 },
    }));
    expect(release).toHaveSuccessfulTx<RequestNftRelease>({
        from: buyer.address,
        to: escrow.address,
    });
    expect(release).toHaveTx<AskToChangeOwnership>({
        from: escrow.address,
        to: nftItem.address,
    });
    expect(readEscrowState(escrow).state).toEqual(3);
}

get fun `test dispute dismissal requires two distinct arbitrators`() {
    val (escrow, seller, _, nftItem, authority, arb1, arb2) = setupEscrow();
    depositNft(escrow, seller, nftItem);
    net.send(authority.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RecordStarsPayment {
            queryId: 9,
            amount: 250,
            telegramChargeHash: 0xabc127,
        },
    }));

    val dispute = net.send(seller.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RaiseDispute { queryId: 10, reasonHash: 0xdef001 },
    }));
    expect(dispute).toHaveSuccessfulTx<RaiseDispute>({
        from: seller.address,
        to: escrow.address,
    });
    expect(readEscrowState(escrow).state).toEqual(7);

    val first = net.send(arb1.address, createMessage({
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
        from: arb1.address,
        to: escrow.address,
    });
    expect(readEscrowState(escrow).state).toEqual(7);

    val second = net.send(arb2.address, createMessage({
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
        from: arb2.address,
        to: escrow.address,
    });
    expect(readEscrowState(escrow).state).toEqual(2);
    expect(readEscrowState(escrow).disputeApprovals).toEqual(3);
}

get fun `test dispute during pending NFT transfer is quarantined`() {
    val (escrow, seller, buyer, nftItem, authority, _, _) = setupEscrow();
    depositNft(escrow, seller, nftItem);
    net.send(authority.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RecordStarsPayment {
            queryId: 13,
            amount: 250,
            telegramChargeHash: 0xabc128,
        },
    }));
    net.send(buyer.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RequestNftRelease { queryId: 14 },
    }));

    val dispute = net.send(buyer.address, createMessage({
        bounce: true,
        value: grams("0.05"),
        dest: escrow.address,
        body: RaiseDispute { queryId: 15, reasonHash: 0xdef003 },
    }));
    expect(dispute).toHaveSuccessfulTx<RaiseDispute>({
        from: buyer.address,
        to: escrow.address,
    });
    expect(readEscrowState(escrow).state).toEqual(8);
}
