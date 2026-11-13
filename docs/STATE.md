# GIFT RADAR — фактическое состояние проекта

Дата: 2026-11-14  
Аудируемый код: платежная ветка PR #51, HEAD до аудита `676297fa7c5897291e0db6060c3aa0371b653ce7`.  
Рабочая ветка документа: `feat/phase-0-state-audit`.  
Статус: инженерная оценка исходников, не независимая сертификация и не runtime-аудит.

## Итог

**NO-GO для реальных денег. Testnet-only.** Ветка содержит значительную реализацию маркетплейса, Ledger, Stars, TON-проверок, споров, возвратов и ручной сверки. Однако наличие кода и успешный CI не доказывают завершённую передачу NFT, on-chain escrow или полный расчёт продавцу.

## Что подтверждается исходниками

| Область | Фактическое состояние |
|---|---|
| Backend | FastAPI, SQLAlchemy, Alembic |
| Telegram auth | Проверка Telegram credentials/initData и серверные сессии в backend |
| Marketplace | Модули инвентаря, листингов, резервов, сделок и споров |
| Ledger | Двойная бухгалтерская модель, платёжные события и удержания |
| Telegram Stars | XTR invoice, pre-checkout, successful payment и refund-компоненты |
| TON | TonAPI и Toncenter адаптеры NFT-наблюдения и сверки |
| TON Proof | Отдельный verifier и привязка подтверждённого кошелька в feature-ветке |
| Споры | Открытие спора, административные решения, очередь ручной сверки |
| Выводы | Код двухэтапного одобрения и внешнего HSM-интерфейса |
| Миграции | Alembic цепочка до 0014 в платежной ветке |
| Frontend | Svelte frontend и TON Connect manifest |
| CI | Для базового HEAD 676297fa run #698 завершился success; это не production approval |

## Не реализовано или не подтверждено

- Развёрнутый и проверенный TON escrow smart contract.
- Полный цикл seller → escrow → buyer с независимым on-chain подтверждением доставки.
- Полный testnet E2E оплаты, передачи NFT и расчёта продавцу.
- Реальная staging-конфигурация PostgreSQL/Redis и прогон миграций на ней.
- Успешный backup/restore drill, мониторинг, алерты и on-call.
- Независимый аудит денежной логики и смарт-контракта.
- Юридическое и налоговое заключения для Казахстана.
- Полный secret scan всей Git history и cloud environments.
- Полный аудит всех публичных маршрутов на rate limiting, object-level authorization, SSRF и mass assignment.
- Полная frontend UX-приёмка и нагрузочные испытания.

## P0 — блокирует реальные деньги

1. **Нет NFT escrow.** Наблюдение NFT двумя провайдерами не блокирует актив и не гарантирует его доставку.
2. **Withdrawal signer жёстко mainnet.** В `withdrawal_signer.py` заявка фиксирует `network="ton-mainnet"`; адрес назначения ограничен mainnet basechain. Это несовместимо с testnet-only требованием.
3. **TON address parser mainnet-only.** В `ton_nft_providers.py` friendly tags ограничены `0x11` и `0x51`; testnet-профиль helper не поддерживает.
4. **Юридические sign-off отсутствуют.** Переменные release gates не являются юридическим заключением.
5. **Нет воспроизводимого полного staging E2E.** CI не заменяет тесты на тестовой сети.
6. **PostgreSQL/Redis и восстановление не подтверждены эксплуатационными доказательствами.**

## P1 — высокий риск перед публичным MVP

1. Централизованный limiter в `security_controls.py` охватывает mutation prefixes `/api/auth/`, `/api/wallet/`, `/api/payments/`, `/api/inventory/`; глобальный rate limit для всех публичных GET и bot entrypoints не доказан.
2. Origin check не является полной CSRF-защитой; нужны тесты всех чувствительных маршрутов и чёткая политика не-браузерных клиентов.
3. CORS allowlist имеет встроенные Cloudflare defaults; необходимо сверить точные staging/production origins.
4. Нужен полный аудит object-level authorization для сделок, инвойсов, депозитов, выводов и админских операций.
5. Нужны property/race тесты Ledger и PostgreSQL-конкурентности.
6. Нужны durable outbox и гарантии повторной доставки уведомлений.
7. Все публичные API изменения должны отражаться в `docs/INTERFACES.md`.

## P2 — до масштабирования

- UX-приёмка каталога, фильтров, истории, графиков, пустых состояний и тем Telegram.
- Публичная статистика, рефералы и промо.
- Оптимизация каталога, кэша, квот и circuit breakers TON-провайдеров.
- Расширение операционной аналитики.

## Security observations

- `app.py` отключает OpenAPI/docs endpoints и использует общий validation error handler без отражения пользовательских значений.
- `security_controls.py` задаёт security headers и `Cache-Control: no-store` для JSON.
- Redis limiter в production fail-closed для перечисленных mutation prefixes при отсутствии Redis.
- `withdrawal_signer.py` использует внешний HSM gateway по архитектуре; наличие и аудит самого сервиса не подтверждены.
- Проверенный TON provider использует фиксированные HTTPS endpoints и timeout; полный SSRF аудит всех сетевых клиентов не завершён.
- Полный secret scan Git history и cloud variables не проводился в рамках этого прохода. Отсутствие секретов не утверждается.

## План завершения Фазы 0

1. Инвентаризировать все маршруты, middleware, webhook и фоновые задачи.
2. Запустить полный набор тестов на актуальном PR HEAD и сохранить CI evidence.
3. Проверить миграции на отдельной PostgreSQL staging базе.
4. Добавить testnet-only policy tests, запрещающие mainnet операции.
5. Провести полный secret scan Git history и dependency scan.
6. Зафиксировать API контракты в `docs/INTERFACES.md`.
7. Закрыть P0/P1 либо оставить их документированными release blockers.

## Следующий этап

После завершения аудита и закрепления testnet-only режима перейти к Фазе 1: оформить одобренную архитектуру в `docs/ESCROW.md`, затем реализовать escrow исключительно в TON testnet. Mainnet операции не включать.

## Ограничения

Документ основан на просмотре исходников платежной ветки PR #51 и ключевых модулей. Он не подтверждает текущий runtime Railway, конфигурацию секретов в облаке или безопасность production. Это не независимый аудит и не юридическое заключение.
