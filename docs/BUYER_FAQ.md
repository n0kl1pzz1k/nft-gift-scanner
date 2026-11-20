# HASSLE NFT | Buyer FAQ and negotiation notes

## What exactly is being sold?
The intended sale covers the complete HASSLE NFT project and all project-specific assets that the seller owns and can transfer: source code, product materials, documentation, deployment configuration, and the supported Telegram/hosting assets listed in the final asset schedule. It is not merely a code license.

## Is the marketplace live?
The public Mini App is deployed and its catalog UI renders. The public catalog currently has zero assets. A live interface is not proof of live commercial transactions.

## Are TON and Telegram Stars payments working?
The codebase contains payment-related modules, but real TON and Telegram Stars payment feature flags are disabled. Do not describe checkout, escrow settlement or seller payouts as live.

## Is the escrow multisig deployed?
No deployed HASSLE production multisig has been verified. CI compiles and tests a pinned upstream TON multisig-v2 implementation and derives the intended configuration. That is not an on-chain deployment.

## Has the project passed an independent audit?
No independent HASSLE application/payment/escrow audit is claimed. The upstream TON multisig source has its own published audit materials, but those do not audit this marketplace's integration or business logic.

## Does the project have users or revenue?
No user base, revenue, paying customers or completed live trades are represented in the sale package unless separately evidenced and verified later.

## What does CI prove?
The latest completed release run passed Svelte/TypeScript checks, frontend build, Playwright browser smoke tests, backend compilation and regression tests, deployment configuration checks, and pinned upstream multisig tests/build/config derivation. CI does not make real payments or transfer NFTs.

## Can the buyer take over hosting and the Telegram bot?
The project can be transferred by supported account ownership transfer or redeployed into buyer-controlled accounts. The exact bot username, Telegram transfer eligibility, Cloudflare/Railway account transfer rules and domain registrar ownership must be confirmed during due diligence. Provider service URLs are not automatically owned domains.

## Are user wallets or seed phrases included?
No. Seed phrases, private keys, wallet passwords and personal wallet access are excluded. The app must never request or store them.

## Is user data included?
Not automatically. Any database export containing personal data requires a lawful basis, appropriate notices/consents where required, minimization and an agreed secure transfer process. A clean schema or sanitized dataset may be used for technical handover.

## What is the repository license?
The repository is public but currently has no LICENSE file. Public visibility does not itself grant reuse rights. The sale must use a written asset purchase and IP assignment agreement, and third-party dependencies remain under their own licenses.

## What should be negotiated?
Agree the exact asset schedule, included/excluded accounts, transfer sequence, payment escrow, acceptance tests, credential rotation, data handling, support period, liability allocation and treatment of third-party services before signing. Do not promise production-ready payments, audited escrow, automatic payouts or revenue.

## Seller's negotiation posture
Present the verified engineering work confidently, but price the project as a deployed software asset with unfinished commercial settlement and compliance gates. Any asking price should be treated as a negotiation position, not a third-party valuation. Avoid claims about valuation multiples or future revenue without evidence.
