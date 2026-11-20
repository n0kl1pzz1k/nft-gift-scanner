# HASSLE NFT | Full-project handover checklist

This checklist is for a complete project sale. Mark an item transferred only after the buyer has verified access and accepted it.

## 1. Contract and asset schedule
- [ ] Signed asset purchase / IP assignment agreement.
- [ ] Exact asset schedule attached, including exclusions and third-party dependencies.
- [ ] Seller confirms ownership or right to transfer each included asset.
- [ ] Buyer confirms acceptance criteria and handover date.

## 2. GitHub source repository
- [ ] Transfer repository ownership to buyer's GitHub account or organization.
- [ ] Verify buyer admin access and ability to clone/push.
- [ ] Review open issues, branches, Actions secrets and collaborator access.
- [ ] Rotate/remove seller access after acceptance, unless a support period is agreed.
- [ ] Decide whether to rename legacy repository slug `nft-gift-scanner`.

## 3. Cloudflare frontend
- [ ] Transfer Cloudflare account/project where supported, or redeploy to buyer's account.
- [ ] Verify Worker name, build assets, routes and environment settings.
- [ ] Recreate/rotate Cloudflare API tokens.
- [ ] Verify public Mini App URL and Telegram launch.

## 4. Railway backend and services
- [ ] Transfer Railway project where supported, or recreate services in buyer account.
- [ ] Verify backend, PostgreSQL and Redis service configuration.
- [ ] Recreate environment variables from a secure inventory, never from committed source.
- [ ] Rotate Railway tokens and database credentials.
- [ ] Verify /health and /ready after transfer.

## 5. Telegram bot and Mini App identity
- [ ] Confirm exact bot username and ownership/administration status.
- [ ] Complete Telegram-supported bot ownership transfer if available.
- [ ] Rotate bot token through Telegram's official controls.
- [ ] Reconfigure webhook URL and secret.
- [ ] Verify /start and Mini App launch.
- [ ] Transfer bot avatar, description and project-specific media where owned.

## 6. Database and user data
- [ ] Agree whether any production data is included.
- [ ] Exclude personal/user data unless lawful basis, notice and contract permit transfer.
- [ ] Create a secured export and verify integrity/hash.
- [ ] Transfer through encrypted, access-controlled channel.
- [ ] Buyer restores into a separate target and verifies schema/row counts.
- [ ] Delete seller-held copies only as agreed and where legally permitted.

## 7. Secrets and integrations
- [ ] Provide a list of required secret names and purpose, not secret values in this document.
- [ ] Buyer generates new bot, provider, database and deployment credentials.
- [ ] Reconfigure TON API/TON Connect settings and allowed origins.
- [ ] Verify Stars provider/webhook setup only if buyer intends to activate it.
- [ ] Never transfer seed phrases, private keys or personal wallet access.

## 8. TON multisig and wallet-related items
- [ ] Provide source/config derivation evidence and current deployment status.
- [ ] Clearly record that no deployed HASSLE production multisig has been verified.
- [ ] Do not treat CI derivation as an on-chain deployment.
- [ ] Any future signer/treasury change requires separate buyer approval and on-chain verification.

## 9. Domains, brand and media
- [ ] List each separately registered domain and registrar account.
- [ ] Transfer domains only through registrar-supported process.
- [ ] Confirm ownership/rights for logo, bot avatar, welcome image and other media.
- [ ] Transfer editable source files where they exist.
- [ ] Note provider-owned workers.dev and Railway hostnames are not independent domain registrations.

## 10. Documentation and operations
- [ ] Transfer README, architecture, limitations, deployment notes and runbooks.
- [ ] Transfer backup/restore scripts and explain manual/provider-side backup boundaries.
- [ ] Transfer monitoring/alert configuration if present.
- [ ] Buyer validates a fresh deployment from source.

## 11. Security and access cleanup
- [ ] Buyer rotates every credential and token.
- [ ] Revoke seller's GitHub, Railway, Cloudflare and third-party access after acceptance.
- [ ] Review admin allowlists and roles.
- [ ] Confirm no private keys or seed phrases were collected.
- [ ] Record final access list and incident contact.

## 12. Acceptance and closeout
- [ ] Buyer verifies Mini App, API, bot, database and deployment pipeline.
- [ ] Buyer acknowledges known limitations and disabled payment functions.
- [ ] Buyer signs handover acceptance.
- [ ] Release purchase funds under the signed agreement.
- [ ] Provide any agreed post-sale support window and scope.
