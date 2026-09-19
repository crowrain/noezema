# Слепая выборка — run EVAL-3d (faa3cded-2eec-4087-bc1e-60c242728b90)

seed=20260915 size=50 sampled=34

> **§22.2: слепая выборка — РУЧНАЯ проверка.** Этот файл — структурная
> проекция выборки: тот же seeded/стратифицированный отбор, что у гейтов
> `blind_provenance_path` / `blind_scope`. Автоматический исход этих двух
> гейтов — **структурная проверка, а не пройденные гейты §22.2**; приёмка
> требует, чтобы человек независимо проверил каждый claim ниже: следует ли
> statement из процитированных фрагментов. См. docs/eval/EVAL-3-freeze.md,
> «Ограничение метода».

## claim `55bf6368-fff9-482d-ba24-b7d65339958a`

- type: `temporal_fact`
- statement: По состоянию на 15 апреля 2026 года количество государств-членов ООН составляет 193.
- epistemic_status: `supported` (head: current)
- grade: `E3`
- assessed_scope: `{"as_of": "2026-04-15", "scope_schema": "host-scope-v1", "source_domains": ["un.org", "wikipedia.org"]}`
- freshness: `due` (as_of 2026-04-15T00:00:00+00:00)

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:13:38.726013+00:00", "scope_schema": "host-scope-v1", "source_domain": "un.org"}`
- source: https://www.un.org/en/about-us (content sha256 `b4e9db77366f85dc…`) chunk `chunk-0`
- fragment:
  > About Us | United Nations
  > Skip to main content
  > Toggle navigation
  > Welcome to the United Nations
  > العربية
  > 中文
  > Nederlands
  > English
  > Français
  > Deutsch
  > Kreyòl
  > हिन्दी
  > Bahasa Indonesia
  > Italiano
  > Polski
  > Português
  > Русский
  > Español
  > Kiswahili
  > Türkçe
  > Українська
  > Peace, dignity and equality 
  > on a healthy planet
  > Search the United Nations
  > Submit Search
  > A-Z Site Index
  > Toggle navigation
  > About Us          
  >  »
  > About Us
  > Member States
  > Main Bodies
  > Secretary-General
  > Secretariat
  > UN System
  > History
  > Emblem and Flag
  > UN Charter
  > UDHR
  > ICJ Statute
  > Nobel Peace Prize
  > Our Work          
  >  »
  > Our Work
  > Peace and Security
  > Human Rights
  > Humanitarian Aid
  > Sustainable Development and Climate
  > International Law
  > Global Issues
  > Documents
  > Official Languages
  > Observances
  > Events and News          
  > Get Involved          
  > General Debate          
  > The UN Pulse          
  > About Us
  > One place where the world's nations can
  > gather
  >  together, 
  > discuss
  >  common problems
  > 
  > 			and 
  > find shared solutions
  > .
  > The United Nations is an international organization founded in 1945. Currently made up of 193 
  > Member States
  > , the 
  > UN and its work
  >  are guided by the purposes and principles contained in its founding 
  > Charter
  > .
  > The UN has evolved over the years to keep pace with a rapidly changing world.
  > But one thing has stayed the same: it remains the one place on Earth where all the world’s nations can gather together, discuss common problems, and find shared solutions that benefit all of humanity.
  > The most impossible job on Earth. So far only 9 people have held the top leadership position in the world’s largest multilateral organization. What does it mean to be 
  > UN Secretary-General
  > ?
  > Member
  > 
  > 			States
  > The UN’s Membership has 
  > grown from the original 51 Member States
  >  in 1945 to the 
  > current 193 Member States
  > .
  > All UN Member States are members of the 
  > General Assembly
  > .  States are admitted to membership by a decision of the General Assembly upon the recommendation of the 
  > Security Council
  > .
  > Secretary-General
  > In the end, it comes down to values [...] W […] (5396 chars more)

### evidence 2: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:15:04.075304+00:00", "scope_schema": "host-scope-v1", "source_domain": "wikipedia.org"}`
- source: https://en.wikipedia.org/wiki/List_of_member_states_of_the_United_Nations (content sha256 `d25dbfa6b0df134e…`) chunk `chunk-0`
- fragment:
  > Member states of the United Nations - Wikipedia
  > Jump to content
  > Main menu
  > Main menu
  > move to sidebar
  > hide
  > 
  > 		Navigation
  > 	
  > Main page
  > Contents
  > Current events
  > Random article
  > About Wikipedia
  > Contact us
  > 
  > 		Contribute
  > 	
  > Help
  > Learn to edit
  > Community portal
  > Recent changes
  > Upload file
  > Special pages
  > Search
  > Search
  > Appearance
  > Donate
  > Create account
  > Log in
  > Personal tools
  > Donate
  > Create account
  > Log in
  > Contents
  > move to sidebar
  > hide
  > (Top)
  > 1
  > Membership
  > 2
  > Original members
  > 3
  > Current members
  > Toggle Current members subsection
  > 3.1
  > Package deal
  > 4
  > Former members
  > Toggle Former members subsection
  > 4.1
  > Republic of China (1945–1971)
  > 4.1.1
  > Bids for readmission as the representative of Taiwan
  > 4.2
  > States that no longer exist
  > 4.2.1
  > British Raj (1945–1947)
  > 4.2.2
  > Czechoslovakia (1945–1992)
  > 4.2.3
  > German Democratic Republic (1973–1990)
  > 4.2.4
  > Tanganyika (1961–1964) and Zanzibar (1963–1964)
  > 4.2.5
  > Soviet Union (1945–1991)
  > 4.2.6
  > United Arab Republic (1958–1961)
  > 4.2.7
  > Democratic Yemen (1967–1990)
  > 4.2.8
  > Yugoslavia / Serbia and Montenegro (1945–2006)
  > 5
  > Suspension, expulsion and withdrawal of members
  > Toggle Suspension, expulsion and withdrawal of members subsection
  > 5.1
  > De facto withdrawal of Indonesia (1965–1966)
  > 6
  > Observers and non-members
  > Toggle Observers and non-members subsection
  > 6.1
  > Observers
  > 6.2
  > Non-member states
  > 7
  > See also
  > 8
  > Notes
  > 9
  > References
  > 10
  > External links
  > Toggle the table of contents
  > Member states of the United Nations
  > 72 languages
  > العربية
  > Azərbaycanca
  > Беларуская
  > Български
  > भोजपुरी
  > Bislama
  > বাংলা
  > Bosanski
  > Català
  > کوردی
  > Čeština
  > Dansk
  > Deutsch
  > Ελληνικά
  > Esperanto
  > Español
  > Eesti
  > Euskara
  > فارسی
  > Suomi
  > Français
  > ગુજરાતી
  > עברית
  > हिन्दी
  > Hrvatski
  > Magyar
  > Հայերեն
  > Bahasa Indonesia
  > Italiano
  > 日本語
  > Jawa
  > ქართული
  > Қазақша
  > ಕನ್ನಡ
  > 한국어
  > Lëtzebuergesch
  > Lietuvių
  > Latviešu
  > मैथिली
  > Basa Banyumasan
  > മലയാളം
  > Монгол
  > मराठी
  > Bahasa Melayu
  > မြန်မာဘာသာ
  > नेपाली
  > Nederlands
  > Norsk bokmål
  > پنجابی
  > پښتو
  > Português
  > Română
  > Русский
  > Srpskohrvatski / српскохрватски
  > တႆး
  > සිංහල
  > Simple English
  > Slovenčina
  > Anarâškielâ
  > Soomaaliga
  > Shqip
  > Српски / srpski
  > Svenska
  > தமிழ்
  > తెలుగు
  >  […] (88500 chars more)

## claim `06f7b36e-9f46-4e62-a63b-8531c69714cf`

- type: `temporal_fact`
- statement: По состоянию на 20 сентября 2026 года последней стабильной версией PostgreSQL является 18.6.
- epistemic_status: `supported` (head: current)
- grade: `E3`
- assessed_scope: `{"as_of": "2026-09-19", "scope_schema": "host-scope-v1", "source_domains": ["postgresql.org", "habr.com"]}`
- freshness: `fresh` (as_of 2026-09-20T00:00:00+00:00)

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:56:47.398568+00:00", "scope_schema": "host-scope-v1", "source_domain": "postgresql.org"}`
- source: https://www.postgresql.org/ (content sha256 `6097be775186d95d…`) chunk `chunk-0`
- fragment:
  > PostgreSQL: The world's most advanced open source database
  > Home
  > About
  > Download
  > Documentation
  > Community
  > Developers
  > Support
  > Donate
  > Your account
  > August 13, 2026: 
  > PostgreSQL 18.6, 17.11, 16.15, 15.19, 14.24 and 19 Beta 3 Released!
  > PostgreSQL: The World's Most Advanced Open Source Relational Database
  > Download 
  > New to PostgreSQL?
  > New to PostgreSQL?
  > 
  >           PostgreSQL is a powerful, open source object-relational database system with over 35 years of active development
  >           that has earned it a strong reputation for reliability, feature robustness, and performance.
  >         
  > 
  >           There is a wealth of information to be found describing how to 
  > install
  >  and 
  > use
  >  PostgreSQL through the 
  > official documentation
  > .
  >           The 
  > open source community
  > 
  >           provides many helpful places to become familiar with PostgreSQL,
  >           discover how it works, and find career opportunities. Learn more on
  >           how to 
  > engage with the community
  > .
  >         
  > Learn More
  > Feature Matrix
  > Governance
  > Latest Releases
  > 2026-08-13 - 
  > 
  > PostgreSQL 19 Beta 3, 18.6, 17.11, 16.15, 15.19 and 14.24 Released!
  > 
  > 
  >           The PostgreSQL Global Development Group has
  >           
  > released an update
  >  to all supported versions
  >           of PostgreSQL, including
  >           
  > 19 Beta 3, 18.6, 17.11, 16.15, 15.19 and 14.24
  > .
  >           This release fixes 28 security vulnerabilities and 
  > 	  many bugs reported over the last several months.
  > 	
  > 
  >           For the more information about this release, please review the
  >           
  > release notes
  > . You can download
  >           PostgreSQL from the 
  > download
  >  page.
  > 	
  > 
  > PostgreSQL 14 will stop receiving fixes on November 12, 2026.
  > If you are running PostgreSQL 14 in a production environment, we suggest that
  > you make plans to upgrade to a newer, supported version of PostgreSQL. Please see our
  > 
  > versioning policy
  >  for more information.
  > 
  > 18.6
  >  · 2026-08-13 · 
  > Notes
  > 17.11
  >  · 2026-08-13 · 
  > Notes
  > 16.15
  >  · 2026-08-13 · 
  > Notes
  > 15.19
  >  · 2026-08-13 · 
  > Notes
  > 14.24
  >  · 2026-08- […] (4048 chars more)

### evidence 2: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:57:02.776725+00:00", "scope_schema": "host-scope-v1", "source_domain": "habr.com"}`
- source: https://habr.com/ru/news/1080592/ (content sha256 `6176cb70151d5199…`) chunk `chunk-0`
- fragment:
  > В PostgreSQL нашли уязвимость с возможностью запуска произвольного кода, которая была в проекте 12 лет / Хабр
  > Хабр
  > Все потоки
  > Поиск
  > Редактировать
  > Настройки
  > Войти
  > Обновить
  > denis-19
  > 10  сен   в 04:59
  > В PostgreSQL нашли уязвимость с возможностью запуска произвольного кода, которая была в проекте 12 лет
  > Время на прочтение
  > 5 мин
  > Охват и читатели
  > 5.9K
  > Информационная безопасность
  >  * 
  > PostgreSQL
  >  * 
  > Тестирование IT-систем
  >  * 
  > Управление продуктом
  >  * 
  > Управление разработкой
  >  * 
  > Исследователи из ИБ‑компании Cyera Research 
  > раскрыли 
  > детали уязвимости под названием PostGREShell (
  > CVE-2026-6471
  > ), позволяющей пользователю PostgreSQL с привилегией REPLICATION загружать произвольные нативные библиотеки в процесс СУБД. Ошибка появилась вместе с механизмом логического декодирования ещё в PostgreSQL 9.4 и оставалась незамеченной около 12 лет. Патч с исправлением проблемы вышел 13 августа 2026 года для PostgreSQL 18.6, 17.11, 16.15, 15.19 и 14.24.
  > В PostgreSQL оценили уязвимость CVE-2026-6471 в 7.2 балла по шкале CVSS. Согласно официальному описанию проблемы, пользователь, не являющийся суперпользователем, но имеющий атрибут REPLICATION, мог через механизм логического декодирования заставить сервер выполнить dlopen() произвольного файла, доступного системной учётной записи PostgreSQL. В результате код из библиотеки выполнялся уже не с SQL‑правами атакующего, а непосредственно внутри процесса сервера БД.
  > Механизм
  > , в котором находилась ошибка из CVE-2026-6471, 
  > появился
  >  в PostgreSQL 9.4, выпущенном 18 декабря 2014 года. В этой версии разработчики впервые 
  > добавили
  >  логическое декодирование WAL, позволяющее преобразовывать журнал изменений базы в поток данных нужного внешнему приложению формата. В дальнейшем эта инфраструктура стала основой для логической репликации и различных систем Change Data Capture. В документации PostgreSQL 
  > описывается
  >  logical decoding как механизм передачи изменений внешним потребителям через logical replication slots и подключаемые output‑плагины.
  > Для созда […] (7803 chars more)

### evidence 3: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T14:29:40.281783+00:00", "scope_schema": "host-scope-v1", "source_domain": "postgresql.org"}`
- source: https://www.postgresql.org/ (content sha256 `6bbbc88fb9bc6fe1…`) chunk `chunk-0`
- fragment:
  > PostgreSQL: The world's most advanced open source database
  > Home
  > About
  > Download
  > Documentation
  > Community
  > Developers
  > Support
  > Donate
  > Your account
  > August 13, 2026: 
  > PostgreSQL 18.6, 17.11, 16.15, 15.19, 14.24 and 19 Beta 3 Released!
  > PostgreSQL: The World's Most Advanced Open Source Relational Database
  > Download 
  > New to PostgreSQL?
  > New to PostgreSQL?
  > 
  >           PostgreSQL is a powerful, open source object-relational database system with over 35 years of active development
  >           that has earned it a strong reputation for reliability, feature robustness, and performance.
  >         
  > 
  >           There is a wealth of information to be found describing how to 
  > install
  >  and 
  > use
  >  PostgreSQL through the 
  > official documentation
  > .
  >           The 
  > open source community
  > 
  >           provides many helpful places to become familiar with PostgreSQL,
  >           discover how it works, and find career opportunities. Learn more on
  >           how to 
  > engage with the community
  > .
  >         
  > Learn More
  > Feature Matrix
  > Governance
  > Latest Releases
  > 2026-08-13 - 
  > 
  > PostgreSQL 19 Beta 3, 18.6, 17.11, 16.15, 15.19 and 14.24 Released!
  > 
  > 
  >           The PostgreSQL Global Development Group has
  >           
  > released an update
  >  to all supported versions
  >           of PostgreSQL, including
  >           
  > 19 Beta 3, 18.6, 17.11, 16.15, 15.19 and 14.24
  > .
  >           This release fixes 28 security vulnerabilities and 
  > 	  many bugs reported over the last several months.
  > 	
  > 
  >           For the more information about this release, please review the
  >           
  > release notes
  > . You can download
  >           PostgreSQL from the 
  > download
  >  page.
  > 	
  > 
  > PostgreSQL 14 will stop receiving fixes on November 12, 2026.
  > If you are running PostgreSQL 14 in a production environment, we suggest that
  > you make plans to upgrade to a newer, supported version of PostgreSQL. Please see our
  > 
  > versioning policy
  >  for more information.
  > 
  > 18.6
  >  · 2026-08-13 · 
  > Notes
  > 17.11
  >  · 2026-08-13 · 
  > Notes
  > 16.15
  >  · 2026-08-13 · 
  > Notes
  > 15.19
  >  · 2026-08-13 · 
  > Notes
  > 14.24
  >  · 2026-08- […] (4048 chars more)

### evidence 4: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T14:29:57.879716+00:00", "scope_schema": "host-scope-v1", "source_domain": "habr.com"}`
- source: https://habr.com/ru/news/1080592/ (content sha256 `ae33ea41053cb568…`) chunk `chunk-0`
- fragment:
  > В PostgreSQL нашли уязвимость с возможностью запуска произвольного кода, которая была в проекте 12 лет / Хабр
  > Хабр
  > Все потоки
  > Поиск
  > Редактировать
  > Настройки
  > Войти
  > Обновить
  > denis-19
  > 10  сен   в 04:59
  > В PostgreSQL нашли уязвимость с возможностью запуска произвольного кода, которая была в проекте 12 лет
  > Время на прочтение
  > 5 мин
  > Охват и читатели
  > 5.9K
  > Информационная безопасность
  >  * 
  > PostgreSQL
  >  * 
  > Тестирование IT-систем
  >  * 
  > Управление продуктом
  >  * 
  > Управление разработкой
  >  * 
  > Исследователи из ИБ‑компании Cyera Research 
  > раскрыли 
  > детали уязвимости под названием PostGREShell (
  > CVE-2026-6471
  > ), позволяющей пользователю PostgreSQL с привилегией REPLICATION загружать произвольные нативные библиотеки в процесс СУБД. Ошибка появилась вместе с механизмом логического декодирования ещё в PostgreSQL 9.4 и оставалась незамеченной около 12 лет. Патч с исправлением проблемы вышел 13 августа 2026 года для PostgreSQL 18.6, 17.11, 16.15, 15.19 и 14.24.
  > В PostgreSQL оценили уязвимость CVE-2026-6471 в 7.2 балла по шкале CVSS. Согласно официальному описанию проблемы, пользователь, не являющийся суперпользователем, но имеющий атрибут REPLICATION, мог через механизм логического декодирования заставить сервер выполнить dlopen() произвольного файла, доступного системной учётной записи PostgreSQL. В результате код из библиотеки выполнялся уже не с SQL‑правами атакующего, а непосредственно внутри процесса сервера БД.
  > Механизм
  > , в котором находилась ошибка из CVE-2026-6471, 
  > появился
  >  в PostgreSQL 9.4, выпущенном 18 декабря 2014 года. В этой версии разработчики впервые 
  > добавили
  >  логическое декодирование WAL, позволяющее преобразовывать журнал изменений базы в поток данных нужного внешнему приложению формата. В дальнейшем эта инфраструктура стала основой для логической репликации и различных систем Change Data Capture. В документации PostgreSQL 
  > описывается
  >  logical decoding как механизм передачи изменений внешним потребителям через logical replication slots и подключаемые output‑плагины.
  > Для созда […] (7803 chars more)

## claim `c49ca212-2aac-476a-94a2-e2c7cb14dd9f`

- type: `temporal_fact`
- statement: Ключевая ставка Банка России составляет 14,00% с 27 июля 2026 года и сохранялась без изменений по состоянию на 11 сентября 2026 года.
- epistemic_status: `supported` (head: current)
- grade: `E3`
- assessed_scope: `{"as_of": "2026-09-19", "scope_schema": "host-scope-v1", "source_domains": ["cbr.ru", "consultant.ru"]}`
- freshness: `fresh` (as_of 2026-09-11T00:00:00+00:00)

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:18:17.098408+00:00", "scope_schema": "host-scope-v1", "source_domain": "consultant.ru"}`
- source: https://www.consultant.ru/legalnews/32063/ (content sha256 `bc59f3649935999b…`) chunk `chunk-0`
- fragment:
  > ЦБ РФ опять снизил ключевую ставку \ КонсультантПлюс
  > Сайт КонсультантПлюс
  > Главное
  > Доступ к ИИ-помощнику из системы КонсультантПлюс
  > "Позиции ФАС и УФАС по спорным вопросам": новые материалы о защите конкуренции
  > Практика ФАС по Закону N 223-ФЗ: на что контролеры обратили внимание в обзорах за апрель 2026 года
  > Все новости
  > Сегодня
  > ЦБ РФ планирует ограничить риски вложений кредитных организаций в цифровые валюты
  > Сегодня
  > Правила дезинфекции меддокументов в "заразных" зонах при чрезвычайных ситуациях уточнены
  > Сегодня
  > О неуказании в справке счетов наниматель узнал из отчета за следующий год – суд отметил взыскание
  > 18 сентября
  > Суд отменил возврат переплаты по больничному, поскольку СФР знал правильные данные о стаже работника
  > 18 сентября
  > Расторжение договора и передача незавершенных работ: суд не обязал подрядчика выставить счет-фактуру
  > 18 сентября
  > Строительство: срок применения прежних норм при экспертизе проектов предложено увеличить до 3 лет
  > 18 сентября
  > Новый расчет целевой субсидии на уплату налогов при оказании медпомощи – проект Минфина
  > 18 сентября
  > За отсутствие электронных перевозочных документов президент поручил временно не штрафовать
  > 18 сентября
  > Президент продлил специальные экономические меры в сфере импорта продуктов и сырья на 2 года
  > 18 сентября
  > Экстренное извещение об инфекции, отравлении или укусе животного: Минздрав утвердил новую форму
  > 18 сентября
  > Праздники и перенос выходных в 2027 году: правительство утвердило график
  > 18 сентября
  > Практика коллегии по экономическим спорам ВС РФ: обзор за август
  > 18 сентября
  > Обоснования бюджетных ассигнований, КВР и направления расходов: Минфин обновил таблицу на 2027 год
  > 18 сентября
  > Работодатель вовремя не сократил рабочую неделю инвалиду – суды обязали оплатить переработки
  > 18 сентября
  > Национальный режим при закупках ряда медизделий и средств связи предложено применять иначе
  > 17 сентября
  > Больничный исходя из МРОТ: суд вернул СФР переплату, когда работодатель не сообщил про 0,13 ставки
  > 17 сентября
  > Минэкономразвития предлагает у […] (9818 chars more)

### evidence 2: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:18:01.448365+00:00", "scope_schema": "host-scope-v1", "source_domain": "cbr.ru"}`
- source: https://cbr.ru/ (content sha256 `67633e5fe27c4928…`) chunk `chunk-0`
- fragment:
  > Центральный банк Российской Федерации | Банк России
  > EN
  > Поиск по сайту
  > EN
  > Поиск по сайту
  > О Банке России
  > Деятельность
  > Денежно-кредитная политика
  > Финансовая стабильность
  > Национальная платежная система
  > Наличное денежное обращение
  > Развитие финансового рынка
  > Развитие финансовых технологий
  > Защита прав потребителей финансовых услуг
  > Информационная безопасность
  > Противодействие недобросовестным практикам
  > Противодействие отмыванию денег и валютный контроль
  > Допуск на финансовый рынок
  > Деловая репутация
  > Исследования
  > Операции Банка России
  > Финансовые рынки
  > Банковский сектор
  > Пенсионные фонды и коллективные инвестиции
  > Страхование
  > Рынок ценных бумаг
  > Эмитенты и корпоративное управление
  > Микрофинансирование
  > Инфраструктура финансового рынка
  > Кредитные истории
  > Сервисы
  > Обратиться в Банк России
  > Проверить участника финансового рынка
  > Список компаний с выявленными признаками нелегальной деятельности на финансовом рынке
  > Проверка уровня риска на платформе «Знай своего клиента»
  > Вопросы и ответы
  > Личный кабинет участника информационного обмена
  > Информация о кредитных рейтингах
  > Конструктор оценки деловой репутации и квалификации
  > Требования и рекомендации к сайтам финансовых организаций
  > Разъяснения
  > Удостоверяющий центр Банка России
  > Технические ресурсы
  > Осторожно: мошенники!
  > Что вы хотите найти?
  > Искать
  > Деятельность
  > Финансовые рынки
  > Документы и данные
  > О Банке России
  > Сервисы
  > Меры защиты финансового рынка
  > +
  > 7 499 300-30-00
  > 8 800 300-30-00
  > 300
  > Бесплатно для звонков с мобильных телефонов
  > Новости
  > Решения Банка России
  > Контактная информация
  > Карта сайта
  > О сайте
  > Обратиться в Банк России
  > RU
  > EN
  > О Банке России
  > Проверить участника финансового рынка
  > Осторожно: мошенники!
  > Центральный банк 
  >  Российской Федерации
  > Обеспечиваем ценовую и финансовую стабильность, создаем условия для устойчивого роста экономики
  > Открытый урок 
  > Зульфии Кахрумановой
  > Вселенная цифрового рубля: 
  > все о новой форме денег
  > 22 сентября, 9:30
  > Цифровой рубль
  > Все о новой форме национальной валюты
  > № 31 (2620)
  > Вестник 
  > Банка России
  > от 16 сентября 2026 года
  > Це […] (2556 chars more)

## claim `d5461857-896a-4382-b209-521cc98b3500`

- type: `temporal_fact`
- statement: По состоянию на 15 апреля 2026 года последней стабильной версией Python является 3.14.7.
- epistemic_status: `supported` (head: current)
- grade: `E3`
- assessed_scope: `{"as_of": "2026-04-15", "scope_schema": "host-scope-v1", "source_domains": ["python.org", "chocolatey.org"]}`
- freshness: `due` (as_of 2026-04-15T00:00:00+00:00)

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:07:02.892676+00:00", "scope_schema": "host-scope-v1", "source_domain": "python.org"}`
- source: https://www.python.org/downloads/ (content sha256 `52ea3a5a1ee755bb…`) chunk `chunk-0`
- fragment:
  > Download Python | Python.org
  > Notice:
  >  This page displays a fallback because interactive scripts did not run. Possible causes include disabled JavaScript or failure to load scripts or stylesheets.
  > Skip to content
  > ▼
  >  Close
  >                 
  > Python
  > PSF
  > Docs
  > PyPI
  > Jobs
  > Community
  > ▲
  >  The Python Network
  >                 
  > Donate
  > ≡
  >  Menu
  > Search This Site
  > 
  >                                     GO
  >                                 
  > A
  >  A
  > Smaller
  > Larger
  > Reset
  > Socialize
  > LinkedIn
  > Mastodon
  > Chat on IRC
  > Twitter
  > About
  > Applications
  > Quotes
  > Getting Started
  > Help
  > Downloads
  > All releases
  > Source code
  > Windows
  > macOS
  > Android
  > iOS
  > Other Platforms
  > License
  > Alternative Implementations
  > Documentation
  > Docs
  > Audio/Visual Talks
  > Beginner's Guide
  > FAQ
  > Non-English Docs
  > PEP Index
  > Python Books
  > Python Essays
  > Community
  > Diversity
  > Mailing Lists
  > IRC
  > Forums
  > PSF Annual Impact Report
  > Python Conferences
  > Special Interest Groups
  > Python Logo
  > Python Wiki
  > Code of Conduct
  > Community Awards
  > Get Involved
  > Shared Stories
  > Success Stories
  > Arts
  > Business
  > Education
  > Engineering
  > Government
  > Scientific
  > Software Development
  > News
  > Python News
  > PSF Newsletter
  > PSF News
  > PyCon US News
  > Python Insider
  > News from the Community
  > Events
  > Python Events
  > User Group Events
  > Python Events Archive
  > User Group Events Archive
  > Submit an Event
  > Download the latest version for Android
  > Download the latest source release
  > Download Python 3.14.7
  > Download the latest version for Windows
  > Download Python install manager
  > Or get the standalone installer for 
  > Python 3.14.7
  > Download the latest version for iOS
  > Download the latest version for macOS
  > Download Python 3.14.7
  > Download the latest version of Python
  > Download Python 3.14.7
  > 
  >   Looking for Python with a different OS? Python for
  >   
  > Windows
  > ,
  >   
  > Linux/Unix
  > ,
  >   
  > macOS
  > ,
  >   
  > Android
  > ,
  >   
  > iOS
  > ,
  >   
  > other
  > 
  >   Want to help test development versions of Python 3.15?
  >   
  > Pre-releases
  > ,
  >   
  > Docker images
  > Active Python releases
  > For more information visit the Python Developer's Guide
  > .
  > Python version
  > Maintenance status
  > First released
  > End of s […] (19255 chars more)

### evidence 2: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:07:24.375761+00:00", "scope_schema": "host-scope-v1", "source_domain": "chocolatey.org"}`
- source: https://community.chocolatey.org/packages/python314 (content sha256 `2e3e2bf47fadeaa5…`) chunk `chunk-0`
- fragment:
  > Chocolatey Software | Python 3.14 3.14.7
  > Resources
  > Watch videos, read documentation, and hear Chocolatey success stories from companies you trust.
  > View Resources
  > Events
  > Find past and upcoming webinars, workshops, and conferences. New events have recently been added!
  > View Events
  > Courses
  > Step-by-step guides for all things Chocolatey! Earn badges as you learn through interactive digital courses.
  > View Courses
  > Join our monthly Unpacking Software livestream to hear about the latest news, chat and opinion on packaging, software deployment and lifecycle management!
  > Learn More
  > Join the Chocolatey Team on our regular monthly stream where we put a spotlight on the most recent Chocolatey product releases. You'll have a chance to have your questions answered in a live Ask Me Anything format.
  > Learn More
  > Join us for the Chocolatey Coding Livestream, where members of our team dive into the heart of open source development by coding live on various Chocolatey projects. Tune in to witness real-time coding, ask questions, and gain insights into the world of package management. Don't miss this opportunity to engage with our team and contribute to the future of Chocolatey!
  > Learn More
  > Webinar from
  > Wednesday, 17 January 2024
  > We are delighted to announce the release of Chocolatey Central Management v0.12.0, featuring seamless Deployment Plan creation, time-saving duplications, insightful Group Details, an upgraded Dashboard, bug fixes, user interface polishing, and refined documentation. As an added bonus we'll have members of our Solutions Engineering team on-hand to dive into some interesting ways you can leverage the new features available!
  > Watch On-Demand
  > Join the Chocolatey Team as we discuss all things Community, what we do, how you can get involved and answer your Chocolatey questions.
  > Watch The Replays
  > Webinar Replay from
  > Wednesday, 30 March 2022
  > At Chocolatey Software we strive for simple, and teaching others. Let us teach you just how simple it could be to keep your 3rd party app […] (141904 chars more)

## claim `8c569397-c6b2-490d-9952-3feeadf85c15`

- type: `temporal_fact`
- statement: Количество государств-членов ООН на текущую дату составляет 193.
- epistemic_status: `supported` (head: current)
- grade: `E3`
- assessed_scope: `{"as_of": "2026-09-19", "scope_schema": "host-scope-v1", "source_domains": ["un.org", "wikipedia.org"]}`
- freshness: `due` (as_of 2026-05-20T00:00:00+00:00)

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:44:13.819283+00:00", "scope_schema": "host-scope-v1", "source_domain": "wikipedia.org"}`
- source: https://ru.wikipedia.org/wiki/%D0%A1%D0%BF%D0%B8%D1%81%D0%BE%D0%BA_%D0%B3%D0%BE%D1%81%D1%83%D0%B4%D0%B0%D1%80%D1%81%D1%82%D0%B2_%E2%80%94_%D1%87%D0%BB%D0%B5%D0%BD%D0%BE%D0%B2_%D0%9E%D0%9E%D0%9D (content sha256 `08776ad02a53275a…`) chunk `chunk-0`
- fragment:
  > Государства — члены ООН — Википедия
  > Перейти к содержанию
  > Главное меню
  > Главное меню
  > переместить в боковую панель
  > скрыть
  > 
  > 		Навигация
  > 	
  > Заглавная страница
  > Содержание
  > Избранные статьи
  > Случайная статья
  > Текущие события
  > 
  > 		Участие
  > 	
  > Сообщить об ошибке
  > Как править статьи
  > Сообщество
  > Форум
  > Справка
  > Свежие правки
  > Новые страницы
  > Служебные страницы
  > 
  > 		На других языках
  > 	
  > العربية
  > Azərbaycanca
  > Беларуская
  > Български
  > भोजपुरी
  > Bislama
  > বাংলা
  > Bosanski
  > Català
  > کوردی
  > Čeština
  > Dansk
  > Deutsch
  > Ελληνικά
  > English
  > Esperanto
  > Español
  > Eesti
  > Euskara
  > فارسی
  > Suomi
  > Français
  > ગુજરાતી
  > עברית
  > हिन्दी
  > Hrvatski
  > Magyar
  > Հայերեն
  > Bahasa Indonesia
  > Italiano
  > 日本語
  > Jawa
  > ქართული
  > Қазақша
  > ಕನ್ನಡ
  > 한국어
  > Lëtzebuergesch
  > Lietuvių
  > Latviešu
  > मैथिली
  > Basa Banyumasan
  > മലയാളം
  > Монгол
  > मराठी
  > Bahasa Melayu
  > မြန်မာဘာသာ
  > नेपाली
  > Nederlands
  > Norsk bokmål
  > پنجابی
  > پښتو
  > Português
  > Română
  > Srpskohrvatski / српскохрватски
  > တႆး
  > සිංහල
  > Simple English
  > Slovenčina
  > Anarâškielâ
  > Soomaaliga
  > Shqip
  > Српски / srpski
  > Svenska
  > தமிழ்
  > తెలుగు
  > ไทย
  > Türkçe
  > Українська
  > اردو
  > Oʻzbekcha / ўзбекча
  > Tiếng Việt
  > 中文
  > Править ссылки
  > Поиск
  > Найти
  > Внешний вид
  > Пожертвовать
  > Создать учётную запись
  > Войти
  > Персональные инструменты
  > Пожертвовать
  > Создать учётную запись
  > Войти
  > Содержание
  > переместить в боковую панель
  > скрыть
  > Начало
  > 1
  > Члены и наблюдатели ООН
  > 2
  > Первоначальные члены ООН
  > Отобразить/Скрыть подраздел Первоначальные члены ООН
  > 2.1
  > «Польский вопрос»
  > 2.2
  > Члены ООН без формальной государственной независимости
  > 3
  > Список первоначальных членов ООН
  > 4
  > Страны, вошедшие в ООН в 1946—2011 годах
  > Отобразить/Скрыть подраздел Страны, вошедшие в ООН в 1946—2011 годах
  > 4.1
  > 1940-е годы
  > 4.2
  > 1950-е годы
  > 4.3
  > 1960-е годы
  > 4.4
  > 1970-е годы
  > 4.5
  > 1980-е годы
  > 4.6
  > 1990-е годы
  > 4.7
  > 2000-е годы
  > 4.8
  > 2010-е годы
  > 5
  > Примечания
  > Отобразить/Скрыть подраздел Примечания
  > 5.1
  > Комментарии
  > 5.2
  > Источники
  > 6
  > Ссылки
  > Отобразить/Скрыть содержание
  > Государства — члены ООН
  > 72 языка
  > العربية
  > Azərbaycanca
  > Беларуская
  > Български
  > भोजपुरी
  > Bislama
  > বাংলা
  > Bosanski
  > Català
  > کوردی
  > Čeština
  > Dansk
  > Deutsch
  > Ελληνικά
  > English
  > Esperanto
  > Español
  > Eesti
  > Euskara
  > فارسی
  > Suomi
  > Français
  > ગુજરા […] (52880 chars more)

### evidence 2: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T14:03:07.712959+00:00", "scope_schema": "host-scope-v1", "source_domain": "un.org"}`
- source: https://www.un.org/en/about-us (content sha256 `addb45b649d9871a…`) chunk `chunk-0`
- fragment:
  > About Us | United Nations
  > Skip to main content
  > Toggle navigation
  > Welcome to the United Nations
  > العربية
  > 中文
  > Nederlands
  > English
  > Français
  > Deutsch
  > Kreyòl
  > हिन्दी
  > Bahasa Indonesia
  > Italiano
  > Polski
  > Português
  > Русский
  > Español
  > Kiswahili
  > Türkçe
  > Українська
  > Peace, dignity and equality 
  > on a healthy planet
  > Search the United Nations
  > Submit Search
  > A-Z Site Index
  > Toggle navigation
  > About Us          
  >  »
  > About Us
  > Member States
  > Main Bodies
  > Secretary-General
  > Secretariat
  > UN System
  > History
  > Emblem and Flag
  > UN Charter
  > UDHR
  > ICJ Statute
  > Nobel Peace Prize
  > Our Work          
  >  »
  > Our Work
  > Peace and Security
  > Human Rights
  > Humanitarian Aid
  > Sustainable Development and Climate
  > International Law
  > Global Issues
  > Documents
  > Official Languages
  > Observances
  > Events and News          
  > Get Involved          
  > General Debate          
  > The UN Pulse          
  > About Us
  > One place where the world's nations can
  > gather
  >  together, 
  > discuss
  >  common problems
  > 
  > 			and 
  > find shared solutions
  > .
  > The United Nations is an international organization founded in 1945. Currently made up of 193 
  > Member States
  > , the 
  > UN and its work
  >  are guided by the purposes and principles contained in its founding 
  > Charter
  > .
  > The UN has evolved over the years to keep pace with a rapidly changing world.
  > But one thing has stayed the same: it remains the one place on Earth where all the world’s nations can gather together, discuss common problems, and find shared solutions that benefit all of humanity.
  > The most impossible job on Earth. So far only 9 people have held the top leadership position in the world’s largest multilateral organization. What does it mean to be 
  > UN Secretary-General
  > ?
  > Member
  > 
  > 			States
  > The UN’s Membership has 
  > grown from the original 51 Member States
  >  in 1945 to the 
  > current 193 Member States
  > .
  > All UN Member States are members of the 
  > General Assembly
  > .  States are admitted to membership by a decision of the General Assembly upon the recommendation of the 
  > Security Council
  > .
  > Secretary-General
  > In the end, it comes down to values [...] W […] (5396 chars more)

## claim `0feefb9e-dff2-4df2-8ff6-0203e6ca7055`

- type: `temporal_fact`
- statement: Последние изменения в Конституцию Российской Федерации были одобрены общероссийским голосованием 1 июля 2020 года.
- epistemic_status: `supported` (head: current)
- grade: `E3`
- assessed_scope: `{"as_of": "2020-07-01", "scope_schema": "host-scope-v1", "source_domains": ["wikipedia.org", "gov.ru"]}`
- freshness: `fresh` (as_of 2020-07-01T00:00:00+00:00)

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T16:12:49.098759+00:00", "scope_schema": "host-scope-v1", "source_domain": "gov.ru"}`
- source: http://duma.gov.ru/legislative/documents/constitution/ (content sha256 `f88f2d22bcf8377a…`) chunk `chunk-0`
- fragment:
  > Конституция РФ
  > 
  >             В социальных сетях
  >         
  > English 
  > Español 
  > 中文 
  > عربي 
  > 
  >                     Поиск
  >                 
  > Государственная Дума
  > Федерального Собрания Российской Федерации
  > 
  >                             ГД
  >                         
  > Новости
  > Структура
  > Фото и видео
  > Сервисы
  > Деятельность
  > Законодательная
  > Представительная
  > Международная
  > 
  >                                 Поиск
  >                             
  > Законотворчество
  > Планирование и документы
  > Рассмотрение
  > Результаты голосований
  > Стенограммы
  > СОЗД
  > Конституция РФ
  > Принята всенародным голосованием 12 декабря 1993 года с изменениями, одобренными в ходе общероссийского голосования 1 июля 2020 года. 
  > Мы, многонациональный народ Российской Федерации,
  > соединенные общей судьбой на своей земле,
  > утверждая права и свободы человека, гражданский мир и согласие,
  > сохраняя исторически сложившееся государственное единство, 
  > исходя из общепризнанных принципов равноправия и самоопределения народов,
  > чтя память предков, передавших нам любовь и уважение к Отечеству, веру в добро и справедливость,
  > возрождая суверенную государственность России и утверждая незыблемость ее демократической основы,
  > стремясь обеспечить благополучие и процветание России, 
  > исходя из ответственности за свою Родину перед нынешним и будущими поколениями,
  > сознавая себя частью мирового сообщества,
  > принимаем КОНСТИТУЦИЮ РОССИЙСКОЙ ФЕДЕРАЦИИ.
  > РАЗДЕЛ ПЕРВЫЙ
  > Глава 1. Основы конституционного строя
  > Статья 1
  > 1. Российская Федерация — Россия есть демократическое федеративное правовое государство с республиканской формой правления.
  > 2. Наименования Российская Федерация и Россия равнозначны.
  > Статья 2
  > Человек, его права и свободы являются высшей ценностью. Признание, соблюдение и защита прав и свобод человека и гражданина — обязанность государства.
  > Статья 3
  > 1. Носителем суверенитета и единственным источником власти в Российской Федерации является ее многонациональный народ.
  > 2. Народ осуществляет свою власть непосредственно, а также через органы государственной власти и орг […] (115004 chars more)

### evidence 2: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T16:12:25.120393+00:00", "scope_schema": "host-scope-v1", "source_domain": "wikipedia.org"}`
- source: https://ru.wikipedia.org/wiki/%D0%9A%D0%BE%D0%BD%D1%81%D1%82%D0%B8%D1%82%D1%83%D1%86%D0%B8%D1%8F_%D0%A0%D0%BE%D1%81%D1%81%D0%B8%D0%B9%D1%81%D0%BA%D0%BE%D0%B9_%D0%A4%D0%B5%D0%B4%D0%B5%D1%80%D0%B0%D1%86%D0%B8%D0%B8 (content sha256 `28e2ecd8fa27785e…`) chunk `chunk-0`
- fragment:
  > Конституция Российской Федерации — Википедия
  > Перейти к содержанию
  > Главное меню
  > Главное меню
  > переместить в боковую панель
  > скрыть
  > 
  > 		Навигация
  > 	
  > Заглавная страница
  > Содержание
  > Избранные статьи
  > Случайная статья
  > Текущие события
  > 
  > 		Участие
  > 	
  > Сообщить об ошибке
  > Как править статьи
  > Сообщество
  > Форум
  > Справка
  > Свежие правки
  > Новые страницы
  > Служебные страницы
  > 
  > 		На других языках
  > 	
  > العربية
  > Авар
  > Azərbaycanca
  > Башҡортса
  > Беларуская (тарашкевіца)
  > Беларуская
  > Български
  > বাংলা
  > Буряад
  > Нохчийн
  > Čeština
  > Чӑвашла
  > Cymraeg
  > Deutsch
  > Ελληνικά
  > English
  > Esperanto
  > Español
  > فارسی
  > Suomi
  > Français
  > Gaeilge
  > עברית
  > Magyar
  > Հայերեն
  > Bahasa Indonesia
  > Íslenska
  > Italiano
  > 日本語
  > 한국어
  > Latviešu
  > Монгол
  > Bahasa Melayu
  > Nederlands
  > Norsk bokmål
  > Polski
  > Português
  > Română
  > Саха тыла
  > Српски / srpski
  > Svenska
  > ไทย
  > Türkçe
  > Татарча / tatarça
  > Українська
  > اردو
  > Oʻzbekcha / ўзбекча
  > Tiếng Việt
  > 中文
  > Править ссылки
  > Поиск
  > Найти
  > Внешний вид
  > Пожертвовать
  > Создать учётную запись
  > Войти
  > Персональные инструменты
  > Пожертвовать
  > Создать учётную запись
  > Войти
  > Содержание
  > переместить в боковую панель
  > скрыть
  > Начало
  > 1
  > История конституции
  > Отобразить/Скрыть подраздел История конституции
  > 1.1
  > Разработка проекта
  > 1.2
  > Принятие (1993)
  > 2
  > Структура
  > 3
  > Конституционные поправки и пересмотр Конституции
  > Отобразить/Скрыть подраздел Конституционные поправки и пересмотр Конституции
  > 3.1
  > Внесение изменений в статью 65 Конституции в связи с изменением наименования субъекта России
  > 3.2
  > Внесение изменений в статью 65 Конституции в связи с изменением состава России
  > 3.3
  > Поправки к главам 3—8 Конституции
  > 3.4
  > Пересмотр положений глав 1, 2 и 9 Конституции
  > 3.5
  > Поправки 2020 года
  > 4
  > Отличия Конституции от законов
  > 5
  > Конституция и ограничение прав и свобод человека и гражданина
  > 6
  > Издания
  > 7
  > Переводы
  > 8
  > Фильмы о Конституции Российской Федерации
  > 9
  > Нумизматика
  > 10
  > Филателия
  > 11
  > См. также
  > 12
  > Примечания
  > 13
  > Литература
  > 14
  > Ссылки
  > Отобразить/Скрыть содержание
  > Конституция Российской Федерации
  > 49 языков
  > العربية
  > Авар
  > Azərbaycanca
  > Башҡортса
  > Беларуская (тарашкевіца)
  > Беларуская
  > Български
  > বাংলা
  > Буряад
  > Нохчийн
  > Čeština
  > Чӑвашла
  >  […] (43585 chars more)

## claim `c077ce2b-cfc7-4794-98e8-56e7f38556e7`

- type: `external_fact`
- statement: На текущую дату в мире 9 государств обладают ядерным оружием.
- epistemic_status: `supported` (head: current)
- grade: `E3`
- assessed_scope: `{"as_of": "2026-09-19", "scope_schema": "host-scope-v1", "source_domains": ["fas.org", "icanw.org"]}`
- freshness: `fresh`

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T16:27:49.396217+00:00", "scope_schema": "host-scope-v1", "source_domain": "fas.org"}`
- source: https://nuke.fas.org/guide/ (content sha256 `a2ba48f4bc33c597…`) chunk `chunk-0`
- fragment:
  > Nuclear Forces Guide
  > The
  > 
  >       Nuclear
  > 
  >       Information
  > 
  >     Project
  > FAS
  >  | 
  > Nuke
  > |||| 
  > Search
  >  | 
  > Join FAS
  > Nuclear Forces Guide
  > Nuclear Weapon States
  > United States
  > Russia / USSR
  > United Kingdom
  > France
  > China
  > India
  > Pakistan
  > Israel
  > North Korea
  > Potential  Proliferators 
  > Algeria
  > Argentina
  > Australia
  > Belarus
  > Brazil
  > Chechnya
  > Cuba
  > Egypt
  > Iran
  > Iraq
  > Japan
  > Kazakhstan
  > Libya
  > Romania
  > Saudi Arabia
  > Serbia
  > South Africa
  > South Korea
  > Sudan
  > Syria
  > Taiwan
  > Ukraine
  > Sources and Resources
  > Summary Table - Nuclear Weapons Capabilities
  > The Nuclear Matters Handbook
  > , Office of the Assistant Secretary of Defense (NCB/MN), 2011 edition
  > Profile of World Uranium Enrichment Programs - 2009
  > , Oak Ridge National Laboratory, April 2009
  > Special National Intelligence Estimate: Prospects for Further Proliferation of Nuclear Weapons
  > ,  23 August 1974
  > SIPRI Yearbook
  > Safeguards Implementation Report for 2003
  > , International Atomic Energy Agency, June 30, 2004
  > Nonproliferation Databases
  > , Nuclear Threat Initiative
  > U.S. Nuclear Weapons Cost Study Project
  > FAS
  >  |
  > 
  > Nuke
  >  |
  > 
  > Search
  >  |
  > 
  > Join FAS
  > 
  > https://fas.org/nuke/guide/
  > 
  > Maintained by Hans M. Kristensen (
  > [email protected]
  > )

### evidence 2: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T16:28:03.209953+00:00", "scope_schema": "host-scope-v1", "source_domain": "icanw.org"}`
- source: https://www.icanw.org/faq_ru_nuclear_powers_nuclear_warheads (content sha256 `f72d1fb76ddb310b…`) chunk `chunk-0`
- fragment:
  > Сколько в мире ядерных держав и сколько боеголовок всего? - ICAN
  > 
  >         About
  >       
  > The campaign
  > People and Structure
  > Partners
  > Nobel Prize
  > History of ICAN
  > Voices
  > Contact
  > 
  >         Resources and Updates
  >       
  > News
  > Resources
  > Donate
  > Shop
  > 
  >         Nuclear Weapons: The Problem
  >       
  > Impact of Nuclear Weapons 
  > Which countries have nuclear weapons?
  > What happens if nuclear weapons are used?
  > About Nuclear Weapons
  > 80 years is enough
  > FAQs
  > 
  >         Our Solution: The Ban
  >       
  > Why a ban?
  > The Treaty
  > How is your country doing?
  > Signature and ratification status
  > First Review Conference
  > 
  >         Help Make it Happen
  >       
  > All Actions
  > Join the Campaign
  > Get your Country on board
  > ICAN Cities Appeal
  > ICAN Parliamentary Pledge
  > Events
  > Donate
  > 5 years in force
  > Nuclear Weapons: The Problem 
  > Impact of Nuclear Weapons 
  > Which countries have nuclear weapons?
  > What happens if nuclear weapons are used?
  > About Nuclear Weapons
  > 80 years is enough
  > FAQs
  > Our Solution: The Ban 
  > Why a ban?
  > The Treaty
  > How is your country doing?
  > Signature and ratification status
  > First Review Conference
  > Help Make it Happen 
  > All Actions
  > Join the Campaign
  > Get your Country on board
  > ICAN Cities Appeal
  > ICAN Parliamentary Pledge
  > Events
  > Donate
  > 5 years in force
  > Home
  > Resources and updates
  > Resources
  > FAQ русский язык
  > Сколько в мире ядерных держав и сколько боеголовок всего?
  > Сколько в мире ядерных держав и сколько боеголовок всего?
  > Насколько разрушительно современное ядерное оружие?
  > Отказалась ли Украина от ядерного оружия?
  > Может ли член НАТО присоединиться к Договору о запрещении ядерного оружия?
  > Как насчет теории “ядерного сдерживания”? Способствует ли ядерное оружие сохранению мира?
  > Что такое Договор о запрещении ядерного оружия?
  > Насколько важен договор, если ни одна из ядерных держав его не подписала?
  > Зачем отказаться от ядерного оружия, когда другие ядерные державы его все еще хранят?
  > Чем важен этот договор для стран, не обладающих ядерным оружием?
  > Что такое Договор о всеобъемлющем запрещении ядерных испытаний и почему Р […] (1049 chars more)

## claim `462c80e2-e8f4-454a-86bc-758446aa271a`

- type: `external_fact`
- statement: Последняя стабильная версия Python составляет 3.14.7.
- epistemic_status: `supported` (head: current)
- grade: `E3`
- assessed_scope: `{"as_of": "2026-09-19", "scope_schema": "host-scope-v1", "source_domains": ["python.org", "chocolatey.org"]}`
- freshness: `fresh`

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T15:13:12.028494+00:00", "scope_schema": "host-scope-v1", "source_domain": "chocolatey.org"}`
- source: https://community.chocolatey.org/packages/python314 (content sha256 `07926a4de2730cac…`) chunk `chunk-0`
- fragment:
  > Chocolatey Software | Python 3.14 3.14.7
  > Resources
  > Watch videos, read documentation, and hear Chocolatey success stories from companies you trust.
  > View Resources
  > Events
  > Find past and upcoming webinars, workshops, and conferences. New events have recently been added!
  > View Events
  > Courses
  > Step-by-step guides for all things Chocolatey! Earn badges as you learn through interactive digital courses.
  > View Courses
  > Join our monthly Unpacking Software livestream to hear about the latest news, chat and opinion on packaging, software deployment and lifecycle management!
  > Learn More
  > Join the Chocolatey Team on our regular monthly stream where we put a spotlight on the most recent Chocolatey product releases. You'll have a chance to have your questions answered in a live Ask Me Anything format.
  > Learn More
  > Join us for the Chocolatey Coding Livestream, where members of our team dive into the heart of open source development by coding live on various Chocolatey projects. Tune in to witness real-time coding, ask questions, and gain insights into the world of package management. Don't miss this opportunity to engage with our team and contribute to the future of Chocolatey!
  > Learn More
  > Webinar from
  > Wednesday, 17 January 2024
  > We are delighted to announce the release of Chocolatey Central Management v0.12.0, featuring seamless Deployment Plan creation, time-saving duplications, insightful Group Details, an upgraded Dashboard, bug fixes, user interface polishing, and refined documentation. As an added bonus we'll have members of our Solutions Engineering team on-hand to dive into some interesting ways you can leverage the new features available!
  > Watch On-Demand
  > Join the Chocolatey Team as we discuss all things Community, what we do, how you can get involved and answer your Chocolatey questions.
  > Watch The Replays
  > Webinar Replay from
  > Wednesday, 30 March 2022
  > At Chocolatey Software we strive for simple, and teaching others. Let us teach you just how simple it could be to keep your 3rd party app […] (141904 chars more)

### evidence 2: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:07:02.892676+00:00", "scope_schema": "host-scope-v1", "source_domain": "python.org"}`
- source: https://www.python.org/downloads/ (content sha256 `52ea3a5a1ee755bb…`) chunk `chunk-0`
- fragment:
  > Download Python | Python.org
  > Notice:
  >  This page displays a fallback because interactive scripts did not run. Possible causes include disabled JavaScript or failure to load scripts or stylesheets.
  > Skip to content
  > ▼
  >  Close
  >                 
  > Python
  > PSF
  > Docs
  > PyPI
  > Jobs
  > Community
  > ▲
  >  The Python Network
  >                 
  > Donate
  > ≡
  >  Menu
  > Search This Site
  > 
  >                                     GO
  >                                 
  > A
  >  A
  > Smaller
  > Larger
  > Reset
  > Socialize
  > LinkedIn
  > Mastodon
  > Chat on IRC
  > Twitter
  > About
  > Applications
  > Quotes
  > Getting Started
  > Help
  > Downloads
  > All releases
  > Source code
  > Windows
  > macOS
  > Android
  > iOS
  > Other Platforms
  > License
  > Alternative Implementations
  > Documentation
  > Docs
  > Audio/Visual Talks
  > Beginner's Guide
  > FAQ
  > Non-English Docs
  > PEP Index
  > Python Books
  > Python Essays
  > Community
  > Diversity
  > Mailing Lists
  > IRC
  > Forums
  > PSF Annual Impact Report
  > Python Conferences
  > Special Interest Groups
  > Python Logo
  > Python Wiki
  > Code of Conduct
  > Community Awards
  > Get Involved
  > Shared Stories
  > Success Stories
  > Arts
  > Business
  > Education
  > Engineering
  > Government
  > Scientific
  > Software Development
  > News
  > Python News
  > PSF Newsletter
  > PSF News
  > PyCon US News
  > Python Insider
  > News from the Community
  > Events
  > Python Events
  > User Group Events
  > Python Events Archive
  > User Group Events Archive
  > Submit an Event
  > Download the latest version for Android
  > Download the latest source release
  > Download Python 3.14.7
  > Download the latest version for Windows
  > Download Python install manager
  > Or get the standalone installer for 
  > Python 3.14.7
  > Download the latest version for iOS
  > Download the latest version for macOS
  > Download Python 3.14.7
  > Download the latest version of Python
  > Download Python 3.14.7
  > 
  >   Looking for Python with a different OS? Python for
  >   
  > Windows
  > ,
  >   
  > Linux/Unix
  > ,
  >   
  > macOS
  > ,
  >   
  > Android
  > ,
  >   
  > iOS
  > ,
  >   
  > other
  > 
  >   Want to help test development versions of Python 3.15?
  >   
  > Pre-releases
  > ,
  >   
  > Docker images
  > Active Python releases
  > For more information visit the Python Developer's Guide
  > .
  > Python version
  > Maintenance status
  > First released
  > End of s […] (19255 chars more)

## claim `49d0ed60-012e-4906-b3cb-73973811493a`

- type: `external_fact`
- statement: Буквенный код японской иены в стандарте ISO 4217 — JPY.
- epistemic_status: `supported` (head: current)
- grade: `E3`
- assessed_scope: `{"as_of": null, "scope_schema": "host-scope-v1", "source_domains": ["wikipedia.org", "cbr.ru"]}`
- freshness: `fresh`

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T16:30:59.414798+00:00", "scope_schema": "host-scope-v1", "source_domain": "wikipedia.org"}`
- source: https://ru.wikipedia.org/wiki/%D0%98%D0%B5%D0%BD%D0%B0 (content sha256 `33a0c923d324d949…`) chunk `chunk-0`
- fragment:
  > Иена — Википедия
  > Перейти к содержанию
  > Главное меню
  > Главное меню
  > переместить в боковую панель
  > скрыть
  > 
  > 		Навигация
  > 	
  > Заглавная страница
  > Содержание
  > Избранные статьи
  > Случайная статья
  > Текущие события
  > 
  > 		Участие
  > 	
  > Сообщить об ошибке
  > Как править статьи
  > Сообщество
  > Форум
  > Справка
  > Свежие правки
  > Новые страницы
  > Служебные страницы
  > 
  > 		На других языках
  > 	
  > Afrikaans
  > Alemannisch
  > Aragonés
  > العربية
  > الدارجة
  > مصرى
  > অসমীয়া
  > Asturianu
  > Azərbaycanca
  > تۆرکجه
  > Башҡортса
  > Boarisch
  > Žemaitėška
  > Bikol Central
  > Беларуская (тарашкевіца)
  > Беларуская
  > Български
  > বাংলা
  > বিষ্ণুপ্রিয়া মণিপুরী
  > Brezhoneg
  > Bosanski
  > Batak Mandailing
  > Буряад
  > Català
  > 閩東語 / Mìng-dĕ̤ng-ngṳ̄
  > Нохчийн
  > کوردی
  > Čeština
  > Словѣньскъ / ⰔⰎⰑⰂⰡⰐⰠⰔⰍⰟ
  > Чӑвашла
  > Cymraeg
  > Dansk
  > Deutsch
  > Zazaki
  > Ελληνικά
  > English
  > Esperanto
  > Español
  > Eesti
  > Euskara
  > فارسی
  > Suomi
  > Français
  > Nordfriisk
  > Furlan
  > Frysk
  > Galego
  > Wayuunaiki
  > 客家語 / Hak-kâ-ngî
  > עברית
  > हिन्दी
  > Fiji Hindi
  > Hrvatski
  > Magyar
  > Հայերեն
  > Bahasa Indonesia
  > Ilokano
  > Ido
  > Íslenska
  > Italiano
  > 日本語
  > Patois
  > Jawa
  > ქართული
  > Қазақша
  > ಕನ್ನಡ
  > 한국어
  > Къарачай-малкъар
  > کٲشُر
  > Коми
  > Kernowek
  > Кыргызча
  > Latina
  > Ladino
  > Lombard
  > ລາວ
  > Lietuvių
  > Latviešu
  > Madhurâ
  > Minangkabau
  > Македонски
  > മലയാളം
  > Монгол
  > मराठी
  > Bahasa Melayu
  > Mirandés
  > မြန်မာဘာသာ
  > مازِرونی
  > Plattdüütsch
  > नेपाल भाषा
  > Nederlands
  > Norsk nynorsk
  > Norsk bokmål
  > Occitan
  > ଓଡ଼ିଆ
  > Ирон
  > ਪੰਜਾਬੀ
  > Polski
  > Piemontèis
  > پنجابی
  > Português
  > Română
  > Tarandíne
  > Русиньскый
  > Саха тыла
  > ᱥᱟᱱᱛᱟᱲᱤ
  > سنڌي
  > Srpskohrvatski / српскохрватски
  > Simple English
  > Slovenčina
  > Slovenščina
  > Shqip
  > Српски / srpski
  > Sunda
  > Svenska
  > Kiswahili
  > தமிழ்
  > ತುಳು
  > తెలుగు
  > Тоҷикӣ
  > ไทย
  > Türkmençe
  > Tagalog
  > Toki pona
  > Tok Pisin
  > Türkçe
  > Татарча / tatarça
  > ئۇيغۇرچە / Uyghurche
  > Українська
  > اردو
  > Oʻzbekcha / ўзбекча
  > Vèneto
  > Vepsän kel’
  > Tiếng Việt
  > Winaray
  > 吴语
  > მარგალური
  > ייִדיש
  > Yorùbá
  > 閩南語 / Bân-lâm-gí
  > 粵語
  > 中文
  > Править ссылки
  > Поиск
  > Найти
  > Внешний вид
  > Пожертвовать
  > Создать учётную запись
  > Войти
  > Персональные инструменты
  > Пожертвовать
  > Создать учётную запись
  > Войти
  > Содержание
  > переместить в боковую панель
  > скрыть
  > Начало
  > 1
  > Название
  > 2
  > История
  > 3
  > Иена как резервная валюта
  > 4
  > Монеты в обращении
  > 5
  > Программа памятных монет 47 префектур Японии
  >  […] (33165 chars more)

### evidence 2: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T16:23:02.575778+00:00", "scope_schema": "host-scope-v1", "source_domain": "cbr.ru"}`
- source: https://www.cbr.ru/eng/currency_base/daily/ (content sha256 `b2b3d7fafe5d64f9…`) chunk `chunk-0`
- fragment:
  > Official exchange rates on selected date | Bank of Russia
  > 12 Neglinnaya Street, Moscow, 107016 Russia
  > 8 800 300-30-00
  > www.cbr.ru
  > RU
  > Search
  > RU
  > Search
  > What do you want to find?
  > Search
  > Activity
  > Financial markets
  > Documents and data
  > About Bank of Russia
  > Services
  > +
  > 7 499 300-30-00
  > 8 800 300-30-00
  > Events
  > Contacts
  > Site map
  > About the Site 
  > About Bank of Russia
  > RU
  > EN
  > Databases
  > Foreign Currency Market
  > Official exchange rates on selected date
  > 19.09.2026
  > 
  > 		  The Central Bank of the Russian Federation has set from  19.09.2026 the following exchange rates of foreign currencies against the ruble  without assuming any liability to buy or sell foreign currency at the rates below		
  > 		
  > Num сode
  > Char сode
  > Unit
  > Currency
  > Rate
  > 036
  > AUD
  > 1
  > Australian Dollar
  > 59.9991
  > 944
  > AZN
  > 1
  > Azerbaijan Manat
  > 49.5279
  > 012
  > DZD
  > 100
  > Algerian Dinar
  > 63.0011
  > 051
  > AMD
  > 100
  > Armenian Dram
  > 23.1668
  > 764
  > THB
  > 10
  > Baht
  > 25.3058
  > 048
  > BHD
  > 1
  > Bahraini Dinar
  > 223.8812
  > 933
  > BYN
  > 1
  > Belarusian Ruble
  > 27.8486
  > 068
  > BOB
  > 10
  > Bolivian Boliviano
  > 84.1134
  > 986
  > BRL
  > 1
  > Brazilian Real
  > 16.3433
  > 410
  > KRW
  > 1000
  > Won
  > 60.9994
  > 344
  > HKD
  > 1
  > Hong Kong Dollar
  > 10.7313
  > 980
  > UAH
  > 10
  > Hryvnia
  > 18.8470
  > 208
  > DKK
  > 1
  > Danish Krone
  > 12.9316
  > 784
  > AED
  > 1
  > UAE Dirham
  > 22.9265
  > 840
  > USD
  > 1
  > US Dollar
  > 84.1975
  > 704
  > VND
  > 10000
  > Dong
  > 32.8422
  > 978
  > EUR
  > 1
  > Euro
  > 96.6671
  > 818
  > EGP
  > 10
  > Egyptian Pound
  > 16.1465
  > 985
  > PLN
  > 1
  > Zloty
  > 22.1584
  > 392
  > JPY
  > 100
  > Yen
  > 53.5948
  > 356
  > INR
  > 100
  > Indian Rupee
  > 87.8971
  > 364
  > IRR
  > 1000000
  > Iranian Rial
  > 50.9878
  > 124
  > CAD
  > 1
  > Canadian Dollar
  > 60.1927
  > 634
  > QAR
  > 1
  > Qatari Rial
  > 23.1312
  > 192
  > CUP
  > 10
  > Cuban peso
  > 35.0823
  > 104
  > MMK
  > 1000
  > Kyat
  > 40.0940
  > 981
  > GEL
  > 1
  > Lari
  > 32.2893
  > 498
  > MDL
  > 10
  > Moldovan Leu
  > 48.0453
  > 566
  > NGN
  > 1000
  > Nigeria Naira
  > 63.2455
  > 554
  > NZD
  > 1
  > New Zealand Dollar
  > 48.1736
  > 934
  > TMT
  > 1
  > Turkmenistan New Manat
  > 24.0564
  > 578
  > NOK
  > 10
  > Norwegian Krone
  > 89.3001
  > 512
  > OMR
  > 1
  > Omani Rial
  > 218.9792
  > 946
  > RON
  > 1
  > Romanian Leu
  > 18.3681
  > 360
  > IDR
  > 10000
  > Rupiah
  > 47.4272
  > 710
  > ZAR
  > 10
  > Rand
  > 51.7712
  > 682
  > SAR
  > 1
  > Saudi Riyal
  > 22.4527
  > 960
  > XDR
  > 1
  > SDR (Special Drawing Right)
  > 114.9927
  > 941
  > RSD
  > 100
  > Serbian Dinar
  > 82.3825
  > 702
  > SGD
  > 1
  > Singapore Dollar
  > 66.0114
  > 417
  > KGS
  > 100
  > Som
  > 96.2 […] (984 chars more)

## claim `74eca12c-71ef-43d3-b59e-f07484d2271b`

- type: `external_fact`
- statement: Количество государств-членов ООН составляет 193.
- epistemic_status: `supported` (head: current)
- grade: `E3`
- assessed_scope: `{"as_of": null, "scope_schema": "host-scope-v1", "source_domains": ["un.org", "wikipedia.org"]}`
- freshness: `fresh`

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T14:35:21.784547+00:00", "scope_schema": "host-scope-v1", "source_domain": "un.org"}`
- source: https://www.un.org/en/about-us (content sha256 `ae6177a5ec21eae6…`) chunk `chunk-0`
- fragment:
  > About Us | United Nations
  > Skip to main content
  > Toggle navigation
  > Welcome to the United Nations
  > العربية
  > 中文
  > Nederlands
  > English
  > Français
  > Deutsch
  > Kreyòl
  > हिन्दी
  > Bahasa Indonesia
  > Italiano
  > Polski
  > Português
  > Русский
  > Español
  > Kiswahili
  > Türkçe
  > Українська
  > Peace, dignity and equality 
  > on a healthy planet
  > Search the United Nations
  > Submit Search
  > A-Z Site Index
  > Toggle navigation
  > About Us          
  >  »
  > About Us
  > Member States
  > Main Bodies
  > Secretary-General
  > Secretariat
  > UN System
  > History
  > Emblem and Flag
  > UN Charter
  > UDHR
  > ICJ Statute
  > Nobel Peace Prize
  > Our Work          
  >  »
  > Our Work
  > Peace and Security
  > Human Rights
  > Humanitarian Aid
  > Sustainable Development and Climate
  > International Law
  > Global Issues
  > Documents
  > Official Languages
  > Observances
  > Events and News          
  > Get Involved          
  > General Debate          
  > The UN Pulse          
  > About Us
  > One place where the world's nations can
  > gather
  >  together, 
  > discuss
  >  common problems
  > 
  > 			and 
  > find shared solutions
  > .
  > The United Nations is an international organization founded in 1945. Currently made up of 193 
  > Member States
  > , the 
  > UN and its work
  >  are guided by the purposes and principles contained in its founding 
  > Charter
  > .
  > The UN has evolved over the years to keep pace with a rapidly changing world.
  > But one thing has stayed the same: it remains the one place on Earth where all the world’s nations can gather together, discuss common problems, and find shared solutions that benefit all of humanity.
  > The most impossible job on Earth. So far only 9 people have held the top leadership position in the world’s largest multilateral organization. What does it mean to be 
  > UN Secretary-General
  > ?
  > Member
  > 
  > 			States
  > The UN’s Membership has 
  > grown from the original 51 Member States
  >  in 1945 to the 
  > current 193 Member States
  > .
  > All UN Member States are members of the 
  > General Assembly
  > .  States are admitted to membership by a decision of the General Assembly upon the recommendation of the 
  > Security Council
  > .
  > Secretary-General
  > In the end, it comes down to values [...] W […] (5396 chars more)

### evidence 2: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:44:13.819283+00:00", "scope_schema": "host-scope-v1", "source_domain": "wikipedia.org"}`
- source: https://ru.wikipedia.org/wiki/%D0%A1%D0%BF%D0%B8%D1%81%D0%BE%D0%BA_%D0%B3%D0%BE%D1%81%D1%83%D0%B4%D0%B0%D1%80%D1%81%D1%82%D0%B2_%E2%80%94_%D1%87%D0%BB%D0%B5%D0%BD%D0%BE%D0%B2_%D0%9E%D0%9E%D0%9D (content sha256 `08776ad02a53275a…`) chunk `chunk-0`
- fragment:
  > Государства — члены ООН — Википедия
  > Перейти к содержанию
  > Главное меню
  > Главное меню
  > переместить в боковую панель
  > скрыть
  > 
  > 		Навигация
  > 	
  > Заглавная страница
  > Содержание
  > Избранные статьи
  > Случайная статья
  > Текущие события
  > 
  > 		Участие
  > 	
  > Сообщить об ошибке
  > Как править статьи
  > Сообщество
  > Форум
  > Справка
  > Свежие правки
  > Новые страницы
  > Служебные страницы
  > 
  > 		На других языках
  > 	
  > العربية
  > Azərbaycanca
  > Беларуская
  > Български
  > भोजपुरी
  > Bislama
  > বাংলা
  > Bosanski
  > Català
  > کوردی
  > Čeština
  > Dansk
  > Deutsch
  > Ελληνικά
  > English
  > Esperanto
  > Español
  > Eesti
  > Euskara
  > فارسی
  > Suomi
  > Français
  > ગુજરાતી
  > עברית
  > हिन्दी
  > Hrvatski
  > Magyar
  > Հայերեն
  > Bahasa Indonesia
  > Italiano
  > 日本語
  > Jawa
  > ქართული
  > Қазақша
  > ಕನ್ನಡ
  > 한국어
  > Lëtzebuergesch
  > Lietuvių
  > Latviešu
  > मैथिली
  > Basa Banyumasan
  > മലയാളം
  > Монгол
  > मराठी
  > Bahasa Melayu
  > မြန်မာဘာသာ
  > नेपाली
  > Nederlands
  > Norsk bokmål
  > پنجابی
  > پښتو
  > Português
  > Română
  > Srpskohrvatski / српскохрватски
  > တႆး
  > සිංහල
  > Simple English
  > Slovenčina
  > Anarâškielâ
  > Soomaaliga
  > Shqip
  > Српски / srpski
  > Svenska
  > தமிழ்
  > తెలుగు
  > ไทย
  > Türkçe
  > Українська
  > اردو
  > Oʻzbekcha / ўзбекча
  > Tiếng Việt
  > 中文
  > Править ссылки
  > Поиск
  > Найти
  > Внешний вид
  > Пожертвовать
  > Создать учётную запись
  > Войти
  > Персональные инструменты
  > Пожертвовать
  > Создать учётную запись
  > Войти
  > Содержание
  > переместить в боковую панель
  > скрыть
  > Начало
  > 1
  > Члены и наблюдатели ООН
  > 2
  > Первоначальные члены ООН
  > Отобразить/Скрыть подраздел Первоначальные члены ООН
  > 2.1
  > «Польский вопрос»
  > 2.2
  > Члены ООН без формальной государственной независимости
  > 3
  > Список первоначальных членов ООН
  > 4
  > Страны, вошедшие в ООН в 1946—2011 годах
  > Отобразить/Скрыть подраздел Страны, вошедшие в ООН в 1946—2011 годах
  > 4.1
  > 1940-е годы
  > 4.2
  > 1950-е годы
  > 4.3
  > 1960-е годы
  > 4.4
  > 1970-е годы
  > 4.5
  > 1980-е годы
  > 4.6
  > 1990-е годы
  > 4.7
  > 2000-е годы
  > 4.8
  > 2010-е годы
  > 5
  > Примечания
  > Отобразить/Скрыть подраздел Примечания
  > 5.1
  > Комментарии
  > 5.2
  > Источники
  > 6
  > Ссылки
  > Отобразить/Скрыть содержание
  > Государства — члены ООН
  > 72 языка
  > العربية
  > Azərbaycanca
  > Беларуская
  > Български
  > भोजपुरी
  > Bislama
  > বাংলা
  > Bosanski
  > Català
  > کوردی
  > Čeština
  > Dansk
  > Deutsch
  > Ελληνικά
  > English
  > Esperanto
  > Español
  > Eesti
  > Euskara
  > فارسی
  > Suomi
  > Français
  > ગુજરા […] (52880 chars more)

## claim `33c1cc65-6fb0-49cb-be76-0631e155166d`

- type: `external_fact`
- statement: По состоянию на 1 января 2026 года количество стран-членов Европейского союза составляет 27.
- epistemic_status: `supported` (head: current)
- grade: `E3`
- assessed_scope: `{"as_of": "2026-01-01", "scope_schema": "host-scope-v1", "source_domains": ["wikipedia.org", "europa.eu"]}`
- freshness: `fresh`

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:10:12.560852+00:00", "scope_schema": "host-scope-v1", "source_domain": "europa.eu"}`
- source: https://european-union.europa.eu/principles-countries-history/facts-and-figures-european-union_en (content sha256 `d603e6ca5ed9eb86…`) chunk `chunk-0`
- fragment:
  > Key facts and figures | European Union
  > Skip to main content
  > English
  > Select your language
  > Close
  > EU official languages
  > bg
  > български
  > es
  > español
  > cs
  > čeština
  > da
  > dansk
  > de
  > Deutsch
  > et
  > eesti
  > el
  > ελληνικά
  > en
  > English
  > fr
  > français
  > ga
  > Gaeilge
  > hr
  > hrvatski
  > it
  > italiano
  > lv
  > latviešu
  > lt
  > lietuvių
  > hu
  > magyar
  > mt
  > Malti
  > nl
  > Nederlands
  > pl
  > polski
  > pt
  > português
  > ro
  > română
  > sk
  > slovenčina
  > sl
  > slovenščina
  > fi
  > suomi
  > sv
  > svenska
  > Other languages
  > ru
  > русский
  > uk
  > yкраїнська
  > Search
  > Search
  > European Union
  > Menu
  > Close
  > Menu
  > Back
  > Previous items
  > Next items
  > Home
  > Principles, countries, history
  > Principles and values
  > Facts and figures on the European Union 
  > EU countries
  > EU enlargement
  > Achievements
  > History of the EU
  > Europe Day
  > Symbols
  > Languages
  > See all
  > Institutions, law, budget
  > Institutions and bodies
  > Leadership 
  > Law
  > Budget
  > The Euro
  > See all
  > Priorities and actions
  > EU priorities
  > Actions by topic
  > EU support for Ukraine
  > See all
  > Live, work, study
  > Living in the EU
  > Travelling in the EU
  > Participate, interact, vote
  > Immigration to the EU
  > Working in the EU
  > Doing business in the EU
  > Import and export
  > Funding, grants, subsidies
  > Public contracts
  > Studying and training in the EU
  > Jobs and traineeships in EU institutions
  > See all
  > News and events
  > Featured news
  > Latest news from EU institutions and bodies
  > Events
  > Visual stories
  > See all
  > Contact the EU
  > Write to us
  > Call us
  > Meet us
  > Contact details: institutions, bodies and agencies
  > Visit a European Union institution
  > Europe and worldwide offices
  > Make a complaint
  > Press contacts
  > Social media channels
  > See all
  > Home
  > Principles, countries, history
  > Facts and figures on the European Union 
  > Facts and figures on the European Union 
  > Page contents
  > The EU and its Member States
  > People, size and open borders
  > Economy, trade and government finances
  > Energy and climate
  > Quality of life, jobs and equality
  > Tourism, languages and education
  > The EU and its Member States
  > Founded
  > : in 1951 
  > after the Second World War
  >  by six countries (Belgium, France, Germany, Italy, Luxembourg, and the Netherlands).
  > Current Member States
  > : 
  > 27 c […] (10335 chars more)

### evidence 2: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:09:38.942389+00:00", "scope_schema": "host-scope-v1", "source_domain": "wikipedia.org"}`
- source: https://en.wikipedia.org/wiki/European_Union (content sha256 `e380b405d855f459…`) chunk `chunk-0`
- fragment:
  > European Union - Wikipedia
  > Jump to content
  > Main menu
  > Main menu
  > move to sidebar
  > hide
  > 
  > 		Navigation
  > 	
  > Main page
  > Contents
  > Current events
  > Random article
  > About Wikipedia
  > Contact us
  > 
  > 		Contribute
  > 	
  > Help
  > Learn to edit
  > Community portal
  > Recent changes
  > Upload file
  > Special pages
  > Search
  > Search
  > Appearance
  > Donate
  > Create account
  > Log in
  > Personal tools
  > Donate
  > Create account
  > Log in
  > Contents
  > move to sidebar
  > hide
  > (Top)
  > 1
  > History
  > Toggle History subsection
  > 1.1
  > Background: World Wars and aftermath
  > 1.2
  > Initial years and the Paris Treaty (1948‍–‍1957)
  > 1.3
  > Treaty of Rome (1958‍–‍1972)
  > 1.4
  > First enlargement and European co-operation (1973‍–‍1993)
  > 1.5
  > Treaties of Maastricht, Amsterdam and Nice (1993‍–‍2004)
  > 1.6
  > Treaty of Lisbon and Brexit (2004‍–‍present)
  > 1.7
  > Timeline
  > 2
  > Politics
  > Toggle Politics subsection
  > 2.1
  > Member states
  > 2.1.1
  > Subdivisions
  > 2.1.2
  > Candidate countries
  > 2.1.3
  > Former members
  > 2.2
  > Governance
  > 2.3
  > Branches of power
  > 2.3.1
  > Executive branch
  > 2.3.2
  > Legislative branch
  > 2.3.3
  > Judicial branch
  > 2.3.4
  > Additional branches
  > 2.4
  > EU ethical governance and Ethics Body
  > 2.5
  > Budget
  > 2.6
  > Law
  > 2.6.1
  > Primary law
  > 2.6.2
  > Secondary law
  > 2.7
  > Foreign relations
  > 2.7.1
  > Humanitarian aid
  > 2.7.2
  > International cooperation and development partnerships
  > 2.8
  > Defence
  > 3
  > Geography
  > Toggle Geography subsection
  > 3.1
  > Climate
  > 3.2
  > Environment
  > 4
  > Economy
  > Toggle Economy subsection
  > 4.1
  > Economic and monetary union
  > 4.1.1
  > Capital Markets Union and financial institutions
  > 4.1.2
  > Eurozone and banking union
  > 4.2
  > Trade
  > 4.2.1
  > Single market
  > 4.2.2
  > Customs union
  > 4.2.3
  > Competition and consumer protection
  > 4.2.4
  > External trade
  > 4.3
  > Energy
  > 4.4
  > Transport
  > 4.4.1
  > Schengen Area
  > 4.5
  > Electronic communications and space
  > 4.6
  > Agriculture and fisheries
  > 4.7
  > Labour
  > 4.8
  > Regional development
  > 5
  > Demographics
  > Toggle Demographics subsection
  > 5.1
  > Population
  > 5.1.1
  > Urbanisation
  > 5.2
  > Languages
  > 5.3
  > Religion
  > 5.4
  > Education and research
  > 5.5
  > Health
  > 5.6
  > Social rights and equality
  > 5.7
  > Freedom, security and justice
  > 6
  > Culture
  > Toggle Culture subsection
  > 6.1
  > Sport
  > 6.2
  > Symbols
  > 6.3
  > Media
  > 6.4
  > Influen […] (227702 chars more)

## claim `47ebcdfc-acf7-4e53-8bec-cd6add25eb6b`

- type: `external_fact`
- statement: Количество букв в современном русском алфавите составляет 33.
- epistemic_status: `supported` (head: current)
- grade: `E3`
- assessed_scope: `{"as_of": "2026-09-19", "scope_schema": "host-scope-v1", "source_domains": ["wikipedia.org", "dkyaspol.ru"]}`
- freshness: `fresh`

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T16:19:42.549534+00:00", "scope_schema": "host-scope-v1", "source_domain": "dkyaspol.ru"}`
- source: https://dkyaspol.ru/2020/05/21/%D1%81%D0%BA%D0%BE%D0%BB%D1%8C%D0%BA%D0%BE-%D0%B1%D1%83%D0%BA%D0%B2-%D0%B2-%D1%80%D1%83%D1%81%D1%81%D0%BA%D0%BE%D0%BC-%D0%B0%D0%BB%D1%84%D0%B0%D0%B2%D0%B8%D1%82%D0%B5/ (content sha256 `1a8f4d01dc7aea28…`) chunk `chunk-0`
- fragment:
  > Сколько букв в русском алфавите? · ДК Ясная Поляна
  > 
  > 		Перейти к содержимому	
  > ДК Ясная Поляна
  > Меню	
  > Главная
  > История
  > Отделы
  > Художественный отдел
  > Хореографические коллективы
  > Ансамбль танца «Россинка»
  > Ансамбль танца «Ясенки»
  > Студия восточного танца «Абаль»
  > Хореографическая студия «Капель»
  > Музыкальные коллективы
  > Народный самодеятельный коллектив, хор русской песни «Сударушки»
  > Немецкий фольклорный ансамбль «Хайматкленге»
  > Вокальный ансамбль «Азбука Хит»
  > Татарский фольклорный ансамбль «Лэйсэн»
  > Фольклорный ансамбль «Жарелье»
  > Детский фольклорный ансамбль «Добряночка»
  > Ансамбль «Виктория»
  > Ансамбль «Бриз»
  > Вокальный коллектив «Веснушки»
  > Коллективы ИЗО и ДПИ
  > Студия изобразительных искусств «Мета»
  > Студия «Православная береста»
  > Клуб по интересам «Мастерица»
  > Коллектив ДПИ «Русский сувенир»
  > Театральные коллективы
  > Театральный коллектив «Волшебный сундучок»
  > Театральный коллектив  «Лицедеи»
  > Театр кукол «Смешарики»
  > Кукольный театр «Петрушка»
  > Национальные центры
  > Центр русской культуры «Истоки»
  > Центр татарской культуры «Яшьлек»
  > Центр немецкой культуры «Хоффнунг»
  > Эстетический центр
  > Детский отдел
  > Спортивный отдел
  > Массовый отдел
  > Отдел досуга
  > Контакты
  > Документы
  > Пушкинская карта
  > ДЕНЬ ПОБЕДЫ
  > Часто задаваемые вопросы
  > Муниципальные услуги
  > Требования к помещениям, в которых предоставляется муниципальная услуга
  > XII открытый областной поэтический фестиваль «Осеннее многоцветье»
  > Опубликовано
  > 2020-05-21
  > 2020-05-21
  >  Автор: 
  > Admin
  > Сколько букв в русском алфавите?
  > Сколько букв в русском алфавите? Многие ли из ваших знакомых, не задумываясь, правильно ответят на этот вопрос?
  > Удивительно, но далеко не все люди, говорящие на русском языке, могут с ходу сказать, сколько всего букв в родном алфавите. Вопрос, который вы видите в заглавии этой темы, рассчитан на первоклассников. Однако учителя утверждают, что примерно 60% — 70% взрослых людей отвечают на него неверно!
  > Многие уверены, что букв 32, возможно, ассоциируя эту цифру с количеством зубов в ротовой полости человека. Некоторые и вовсе путают с другими алфа […] (9350 chars more)

### evidence 2: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T16:19:18.676550+00:00", "scope_schema": "host-scope-v1", "source_domain": "wikipedia.org"}`
- source: https://ru.wikipedia.org/wiki/%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9_%D0%B0%D0%BB%D1%84%D0%B0%D0%B2%D0%B8%D1%82 (content sha256 `3900aa296d1c95df…`) chunk `chunk-0`
- fragment:
  > Русский алфавит — Википедия
  > Перейти к содержанию
  > Главное меню
  > Главное меню
  > переместить в боковую панель
  > скрыть
  > 
  > 		Навигация
  > 	
  > Заглавная страница
  > Содержание
  > Избранные статьи
  > Случайная статья
  > Текущие события
  > 
  > 		Участие
  > 	
  > Сообщить об ошибке
  > Как править статьи
  > Сообщество
  > Форум
  > Справка
  > Свежие правки
  > Новые страницы
  > Служебные страницы
  > 
  > 		На других языках
  > 	
  > العربية
  > Asturianu
  > Azərbaycanca
  > Беларуская (тарашкевіца)
  > Беларуская
  > Български
  > Català
  > Čeština
  > Чӑвашла
  > Dansk
  > Deutsch
  > Ελληνικά
  > English
  > Español
  > Eesti
  > فارسی
  > Français
  > Gagauz
  > Galego
  > עברית
  > Magyar
  > Հայերեն
  > Bahasa Indonesia
  > Iñupiatun
  > Italiano
  > 日本語
  > ქართული
  > Қазақша
  > 한국어
  > Къарачай-малкъар
  > Lëtzebuergesch
  > Ligure
  > Lietuvių
  > Latviešu
  > Македонски
  > ꯃꯤꯇꯩ ꯂꯣꯟ
  > Mirandés
  > Nāhuatl
  > Nederlands
  > Norsk nynorsk
  > Norsk bokmål
  > Polski
  > Português
  > Română
  > Simple English
  > Slovenčina
  > Shqip
  > Српски / srpski
  > Svenska
  > Тоҷикӣ
  > ትግርኛ
  > Türkçe
  > Українська
  > Tiếng Việt
  > 閩南語 / Bân-lâm-gí
  > 粵語
  > 中文
  > Править ссылки
  > Поиск
  > Найти
  > Внешний вид
  > Пожертвовать
  > Создать учётную запись
  > Войти
  > Персональные инструменты
  > Пожертвовать
  > Создать учётную запись
  > Войти
  > Содержание
  > переместить в боковую панель
  > скрыть
  > Начало
  > 1
  > Алфавит
  > 2
  > Происхождение и история алфавита
  > 3
  > Происхождение и история букв
  > Отобразить/Скрыть подраздел Происхождение и история букв
  > 3.1
  > Хронология изменений
  > 3.2
  > Судьба отдельных букв в XVIII—XX веках
  > 4
  > Электронное представление
  > 5
  > Русская раскладка клавиатуры
  > 6
  > См. также
  > 7
  > Примечания
  > Отобразить/Скрыть подраздел Примечания
  > 7.1
  > Комментарии
  > 7.2
  > Источники
  > 8
  > Литература
  > 9
  > Ссылки
  > Отобразить/Скрыть содержание
  > Русский алфавит
  > 57 языков
  > العربية
  > Asturianu
  > Azərbaycanca
  > Беларуская (тарашкевіца)
  > Беларуская
  > Български
  > Català
  > Čeština
  > Чӑвашла
  > Dansk
  > Deutsch
  > Ελληνικά
  > English
  > Español
  > Eesti
  > فارسی
  > Français
  > Gagauz
  > Galego
  > עברית
  > Magyar
  > Հայերեն
  > Bahasa Indonesia
  > Iñupiatun
  > Italiano
  > 日本語
  > ქართული
  > Қазақша
  > 한국어
  > Къарачай-малкъар
  > Lëtzebuergesch
  > Ligure
  > Lietuvių
  > Latviešu
  > Македонски
  > ꯃꯤꯇꯩ ꯂꯣꯟ
  > Mirandés
  > Nāhuatl
  > Nederlands
  > Norsk nynorsk
  > Norsk bokmål
  > Polski
  > Português
  > Română
  > Simple English
  > Slovenčina
  > Shqip
  > Српски / srpski
  > Svenska
  > Тоҷикӣ
  > ትግርኛ
  > Türkç […] (26909 chars more)

## claim `0ca9b948-fc7c-4bef-a594-d6a5594fa478`

- type: `external_fact`
- statement: Количество государств-членов Европейского союза составляет 27.
- epistemic_status: `supported` (head: current)
- grade: `E3`
- assessed_scope: `{"as_of": "2026-09-19", "scope_schema": "host-scope-v1", "source_domains": ["wikipedia.org", "europa.eu"]}`
- freshness: `fresh`

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:10:12.560852+00:00", "scope_schema": "host-scope-v1", "source_domain": "europa.eu"}`
- source: https://european-union.europa.eu/principles-countries-history/facts-and-figures-european-union_en (content sha256 `d603e6ca5ed9eb86…`) chunk `chunk-0`
- fragment:
  > Key facts and figures | European Union
  > Skip to main content
  > English
  > Select your language
  > Close
  > EU official languages
  > bg
  > български
  > es
  > español
  > cs
  > čeština
  > da
  > dansk
  > de
  > Deutsch
  > et
  > eesti
  > el
  > ελληνικά
  > en
  > English
  > fr
  > français
  > ga
  > Gaeilge
  > hr
  > hrvatski
  > it
  > italiano
  > lv
  > latviešu
  > lt
  > lietuvių
  > hu
  > magyar
  > mt
  > Malti
  > nl
  > Nederlands
  > pl
  > polski
  > pt
  > português
  > ro
  > română
  > sk
  > slovenčina
  > sl
  > slovenščina
  > fi
  > suomi
  > sv
  > svenska
  > Other languages
  > ru
  > русский
  > uk
  > yкраїнська
  > Search
  > Search
  > European Union
  > Menu
  > Close
  > Menu
  > Back
  > Previous items
  > Next items
  > Home
  > Principles, countries, history
  > Principles and values
  > Facts and figures on the European Union 
  > EU countries
  > EU enlargement
  > Achievements
  > History of the EU
  > Europe Day
  > Symbols
  > Languages
  > See all
  > Institutions, law, budget
  > Institutions and bodies
  > Leadership 
  > Law
  > Budget
  > The Euro
  > See all
  > Priorities and actions
  > EU priorities
  > Actions by topic
  > EU support for Ukraine
  > See all
  > Live, work, study
  > Living in the EU
  > Travelling in the EU
  > Participate, interact, vote
  > Immigration to the EU
  > Working in the EU
  > Doing business in the EU
  > Import and export
  > Funding, grants, subsidies
  > Public contracts
  > Studying and training in the EU
  > Jobs and traineeships in EU institutions
  > See all
  > News and events
  > Featured news
  > Latest news from EU institutions and bodies
  > Events
  > Visual stories
  > See all
  > Contact the EU
  > Write to us
  > Call us
  > Meet us
  > Contact details: institutions, bodies and agencies
  > Visit a European Union institution
  > Europe and worldwide offices
  > Make a complaint
  > Press contacts
  > Social media channels
  > See all
  > Home
  > Principles, countries, history
  > Facts and figures on the European Union 
  > Facts and figures on the European Union 
  > Page contents
  > The EU and its Member States
  > People, size and open borders
  > Economy, trade and government finances
  > Energy and climate
  > Quality of life, jobs and equality
  > Tourism, languages and education
  > The EU and its Member States
  > Founded
  > : in 1951 
  > after the Second World War
  >  by six countries (Belgium, France, Germany, Italy, Luxembourg, and the Netherlands).
  > Current Member States
  > : 
  > 27 c […] (10335 chars more)

### evidence 2: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T14:32:34.590671+00:00", "scope_schema": "host-scope-v1", "source_domain": "wikipedia.org"}`
- source: https://en.wikipedia.org/wiki/European_Union (content sha256 `b1bbdcca8432ad4a…`) chunk `chunk-0`
- fragment:
  > European Union - Wikipedia
  > Jump to content
  > Main menu
  > Main menu
  > move to sidebar
  > hide
  > 
  > 		Navigation
  > 	
  > Main page
  > Contents
  > Current events
  > Random article
  > About Wikipedia
  > Contact us
  > 
  > 		Contribute
  > 	
  > Help
  > Learn to edit
  > Community portal
  > Recent changes
  > Upload file
  > Special pages
  > Search
  > Search
  > Appearance
  > Donate
  > Create account
  > Log in
  > Personal tools
  > Donate
  > Create account
  > Log in
  > Contents
  > move to sidebar
  > hide
  > (Top)
  > 1
  > History
  > Toggle History subsection
  > 1.1
  > Background: World Wars and aftermath
  > 1.2
  > Initial years and the Paris Treaty (1948‍–‍1957)
  > 1.3
  > Treaty of Rome (1958‍–‍1972)
  > 1.4
  > First enlargement and European co-operation (1973‍–‍1993)
  > 1.5
  > Treaties of Maastricht, Amsterdam and Nice (1993‍–‍2004)
  > 1.6
  > Treaty of Lisbon and Brexit (2004‍–‍present)
  > 1.7
  > Timeline
  > 2
  > Politics
  > Toggle Politics subsection
  > 2.1
  > Member states
  > 2.1.1
  > Subdivisions
  > 2.1.2
  > Candidate countries
  > 2.1.3
  > Former members
  > 2.2
  > Governance
  > 2.3
  > Branches of power
  > 2.3.1
  > Executive branch
  > 2.3.2
  > Legislative branch
  > 2.3.3
  > Judicial branch
  > 2.3.4
  > Additional branches
  > 2.4
  > EU ethical governance and Ethics Body
  > 2.5
  > Budget
  > 2.6
  > Law
  > 2.6.1
  > Primary law
  > 2.6.2
  > Secondary law
  > 2.7
  > Foreign relations
  > 2.7.1
  > Humanitarian aid
  > 2.7.2
  > International cooperation and development partnerships
  > 2.8
  > Defence
  > 3
  > Geography
  > Toggle Geography subsection
  > 3.1
  > Climate
  > 3.2
  > Environment
  > 4
  > Economy
  > Toggle Economy subsection
  > 4.1
  > Economic and monetary union
  > 4.1.1
  > Capital Markets Union and financial institutions
  > 4.1.2
  > Eurozone and banking union
  > 4.2
  > Trade
  > 4.2.1
  > Single market
  > 4.2.2
  > Customs union
  > 4.2.3
  > Competition and consumer protection
  > 4.2.4
  > External trade
  > 4.3
  > Energy
  > 4.4
  > Transport
  > 4.4.1
  > Schengen Area
  > 4.5
  > Electronic communications and space
  > 4.6
  > Agriculture and fisheries
  > 4.7
  > Labour
  > 4.8
  > Regional development
  > 5
  > Demographics
  > Toggle Demographics subsection
  > 5.1
  > Population
  > 5.1.1
  > Urbanisation
  > 5.2
  > Languages
  > 5.3
  > Religion
  > 5.4
  > Education and research
  > 5.5
  > Health
  > 5.6
  > Social rights and equality
  > 5.7
  > Freedom, security and justice
  > 6
  > Culture
  > Toggle Culture subsection
  > 6.1
  > Sport
  > 6.2
  > Symbols
  > 6.3
  > Media
  > 6.4
  > Influen […] (227702 chars more)

## claim `c9ec8ab8-7521-4c38-828d-755240dde23e`

- type: `external_fact`
- statement: На текущий момент Европейский союз включает 27 государств-членов.
- epistemic_status: `supported` (head: current)
- grade: `E3`
- assessed_scope: `{"as_of": "2026-09-19", "scope_schema": "host-scope-v1", "source_domains": ["wikipedia.org", "europa.eu"]}`
- freshness: `fresh`

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:26:36.572343+00:00", "scope_schema": "host-scope-v1", "source_domain": "wikipedia.org"}`
- source: https://en.wikipedia.org/wiki/European_Union (content sha256 `91ded1bda879ee17…`) chunk `chunk-0`
- fragment:
  > European Union - Wikipedia
  > Jump to content
  > Main menu
  > Main menu
  > move to sidebar
  > hide
  > 
  > 		Navigation
  > 	
  > Main page
  > Contents
  > Current events
  > Random article
  > About Wikipedia
  > Contact us
  > 
  > 		Contribute
  > 	
  > Help
  > Learn to edit
  > Community portal
  > Recent changes
  > Upload file
  > Special pages
  > Search
  > Search
  > Appearance
  > Donate
  > Create account
  > Log in
  > Personal tools
  > Donate
  > Create account
  > Log in
  > Contents
  > move to sidebar
  > hide
  > (Top)
  > 1
  > History
  > Toggle History subsection
  > 1.1
  > Background: World Wars and aftermath
  > 1.2
  > Initial years and the Paris Treaty (1948‍–‍1957)
  > 1.3
  > Treaty of Rome (1958‍–‍1972)
  > 1.4
  > First enlargement and European co-operation (1973‍–‍1993)
  > 1.5
  > Treaties of Maastricht, Amsterdam and Nice (1993‍–‍2004)
  > 1.6
  > Treaty of Lisbon and Brexit (2004‍–‍present)
  > 1.7
  > Timeline
  > 2
  > Politics
  > Toggle Politics subsection
  > 2.1
  > Member states
  > 2.1.1
  > Subdivisions
  > 2.1.2
  > Candidate countries
  > 2.1.3
  > Former members
  > 2.2
  > Governance
  > 2.3
  > Branches of power
  > 2.3.1
  > Executive branch
  > 2.3.2
  > Legislative branch
  > 2.3.3
  > Judicial branch
  > 2.3.4
  > Additional branches
  > 2.4
  > EU ethical governance and Ethics Body
  > 2.5
  > Budget
  > 2.6
  > Law
  > 2.6.1
  > Primary law
  > 2.6.2
  > Secondary law
  > 2.7
  > Foreign relations
  > 2.7.1
  > Humanitarian aid
  > 2.7.2
  > International cooperation and development partnerships
  > 2.8
  > Defence
  > 3
  > Geography
  > Toggle Geography subsection
  > 3.1
  > Climate
  > 3.2
  > Environment
  > 4
  > Economy
  > Toggle Economy subsection
  > 4.1
  > Economic and monetary union
  > 4.1.1
  > Capital Markets Union and financial institutions
  > 4.1.2
  > Eurozone and banking union
  > 4.2
  > Trade
  > 4.2.1
  > Single market
  > 4.2.2
  > Customs union
  > 4.2.3
  > Competition and consumer protection
  > 4.2.4
  > External trade
  > 4.3
  > Energy
  > 4.4
  > Transport
  > 4.4.1
  > Schengen Area
  > 4.5
  > Electronic communications and space
  > 4.6
  > Agriculture and fisheries
  > 4.7
  > Labour
  > 4.8
  > Regional development
  > 5
  > Demographics
  > Toggle Demographics subsection
  > 5.1
  > Population
  > 5.1.1
  > Urbanisation
  > 5.2
  > Languages
  > 5.3
  > Religion
  > 5.4
  > Education and research
  > 5.5
  > Health
  > 5.6
  > Social rights and equality
  > 5.7
  > Freedom, security and justice
  > 6
  > Culture
  > Toggle Culture subsection
  > 6.1
  > Sport
  > 6.2
  > Symbols
  > 6.3
  > Media
  > 6.4
  > Influen […] (227702 chars more)

### evidence 2: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:10:12.560852+00:00", "scope_schema": "host-scope-v1", "source_domain": "europa.eu"}`
- source: https://european-union.europa.eu/principles-countries-history/facts-and-figures-european-union_en (content sha256 `d603e6ca5ed9eb86…`) chunk `chunk-0`
- fragment:
  > Key facts and figures | European Union
  > Skip to main content
  > English
  > Select your language
  > Close
  > EU official languages
  > bg
  > български
  > es
  > español
  > cs
  > čeština
  > da
  > dansk
  > de
  > Deutsch
  > et
  > eesti
  > el
  > ελληνικά
  > en
  > English
  > fr
  > français
  > ga
  > Gaeilge
  > hr
  > hrvatski
  > it
  > italiano
  > lv
  > latviešu
  > lt
  > lietuvių
  > hu
  > magyar
  > mt
  > Malti
  > nl
  > Nederlands
  > pl
  > polski
  > pt
  > português
  > ro
  > română
  > sk
  > slovenčina
  > sl
  > slovenščina
  > fi
  > suomi
  > sv
  > svenska
  > Other languages
  > ru
  > русский
  > uk
  > yкраїнська
  > Search
  > Search
  > European Union
  > Menu
  > Close
  > Menu
  > Back
  > Previous items
  > Next items
  > Home
  > Principles, countries, history
  > Principles and values
  > Facts and figures on the European Union 
  > EU countries
  > EU enlargement
  > Achievements
  > History of the EU
  > Europe Day
  > Symbols
  > Languages
  > See all
  > Institutions, law, budget
  > Institutions and bodies
  > Leadership 
  > Law
  > Budget
  > The Euro
  > See all
  > Priorities and actions
  > EU priorities
  > Actions by topic
  > EU support for Ukraine
  > See all
  > Live, work, study
  > Living in the EU
  > Travelling in the EU
  > Participate, interact, vote
  > Immigration to the EU
  > Working in the EU
  > Doing business in the EU
  > Import and export
  > Funding, grants, subsidies
  > Public contracts
  > Studying and training in the EU
  > Jobs and traineeships in EU institutions
  > See all
  > News and events
  > Featured news
  > Latest news from EU institutions and bodies
  > Events
  > Visual stories
  > See all
  > Contact the EU
  > Write to us
  > Call us
  > Meet us
  > Contact details: institutions, bodies and agencies
  > Visit a European Union institution
  > Europe and worldwide offices
  > Make a complaint
  > Press contacts
  > Social media channels
  > See all
  > Home
  > Principles, countries, history
  > Facts and figures on the European Union 
  > Facts and figures on the European Union 
  > Page contents
  > The EU and its Member States
  > People, size and open borders
  > Economy, trade and government finances
  > Energy and climate
  > Quality of life, jobs and equality
  > Tourism, languages and education
  > The EU and its Member States
  > Founded
  > : in 1951 
  > after the Second World War
  >  by six countries (Belgium, France, Germany, Italy, Luxembourg, and the Netherlands).
  > Current Member States
  > : 
  > 27 c […] (10335 chars more)

## claim `e6ee5935-eec5-41ae-b0f8-9e356570cbc2`

- type: `local_observation`
- statement: В файле notes/reading.md содержится список из 3 книг.
- epistemic_status: `supported` (head: current)
- grade: `E2`
- assessed_scope: `{"as_of": null, "scope_schema": "host-scope-v1", "source_domains": []}`
- freshness: `fresh`

### evidence 1: `local_observation` / `supports`
- scope: `{"as_of": "2026-09-19T14:17:00.779480+00:00", "scope_schema": "host-scope-v1", "source_domain": null}`
- observation artifact: `aeb40611-0793-4102-8793-21a4fed28b16`
- fragment: *(в artifact-хранилище недоступен)*

## claim `b97cb869-4af5-44c4-95d8-6de4823a2d43`

- type: `local_observation`
- statement: Файл notes/glossary.md существует в workspace и содержит две строки текста.
- epistemic_status: `supported` (head: current)
- grade: `E2`
- assessed_scope: `{"as_of": null, "scope_schema": "host-scope-v1", "source_domains": []}`
- freshness: `fresh`

### evidence 1: `local_observation` / `supports`
- scope: `{"as_of": "2026-09-19T13:36:42.016877+00:00", "scope_schema": "host-scope-v1", "source_domain": null}`
- observation artifact: `902138a0-b530-4600-8f2f-9640cc614b79`
- fragment: *(в artifact-хранилище недоступен)*

### evidence 2: `local_observation` / `supports`
- scope: `{"as_of": "2026-09-19T13:36:42.016877+00:00", "scope_schema": "host-scope-v1", "source_domain": null}`
- observation artifact: `7d4398d5-7487-46e1-a2e5-9a2f325778fe`
- fragment: *(в artifact-хранилище недоступен)*

## claim `98104359-427b-42ca-be95-256378d84fd5`

- type: `local_observation`
- statement: Первая книга в списке файла notes/reading.md — «Вой с пустого места».
- epistemic_status: `supported` (head: current)
- grade: `E2`
- assessed_scope: `{"as_of": null, "scope_schema": "host-scope-v1", "source_domains": []}`
- freshness: `fresh`

### evidence 1: `local_observation` / `supports`
- scope: `{"as_of": "2026-09-19T15:07:39.931212+00:00", "scope_schema": "host-scope-v1", "source_domain": null}`
- observation artifact: `aeb40611-0793-4102-8793-21a4fed28b16`
- fragment: *(в artifact-хранилище недоступен)*

## claim `53c13c4d-31fb-4beb-98f6-99cb6616c85d`

- type: `local_observation`
- statement: В файле notes/todo.md выполнено 1 задача (Задача 1), не выполнено 2 задачи (Задача 2, Задача 3).
- epistemic_status: `supported` (head: current)
- grade: `E2`
- assessed_scope: `{"as_of": null, "scope_schema": "host-scope-v1", "source_domains": []}`
- freshness: `fresh`

### evidence 1: `local_observation` / `supports`
- scope: `{"as_of": "2026-09-19T15:09:37.831649+00:00", "scope_schema": "host-scope-v1", "source_domain": null}`
- observation artifact: `96fffeff-4b15-47dd-937f-4bb7b964d6b7`
- fragment: *(в artifact-хранилище недоступен)*

## claim `1920edf7-6346-43cb-9e02-d0e036295894`

- type: `temporal_fact`
- statement: Ключевая ставка Банка России составляет 14,00% по состоянию на 11 сентября 2026 года; значение не изменилось по сравнению с решением от 27 июля 2026 года, предыдущее значение (14,25%) было установлено в июне 2026 года.
- epistemic_status: `hypothesis` (head: current)
- grade: `E1`
- assessed_scope: `{"as_of": "2026-09-19", "scope_schema": "host-scope-v1", "source_domains": ["cbr.ru", "consultant.ru"]}`
- freshness: `fresh` (as_of 2026-09-11T00:00:00+00:00)

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T14:44:41.572969+00:00", "scope_schema": "host-scope-v1", "source_domain": "consultant.ru"}`
- source: https://www.consultant.ru/legalnews/32063/ (content sha256 `b5af03173efc40d3…`) chunk `chunk-0`
- fragment:
  > ЦБ РФ опять снизил ключевую ставку \ КонсультантПлюс
  > Сайт КонсультантПлюс
  > Главное
  > Доступ к ИИ-помощнику из системы КонсультантПлюс
  > "Позиции ФАС и УФАС по спорным вопросам": новые материалы о защите конкуренции
  > Практика ФАС по Закону N 223-ФЗ: на что контролеры обратили внимание в обзорах за апрель 2026 года
  > Все новости
  > Сегодня
  > ЦБ РФ планирует ограничить риски вложений кредитных организаций в цифровые валюты
  > Сегодня
  > Правила дезинфекции меддокументов в "заразных" зонах при чрезвычайных ситуациях уточнены
  > Сегодня
  > О неуказании в справке счетов наниматель узнал из отчета за следующий год – суд отменил взыскание
  > 18 сентября
  > Суд отменил возврат переплаты по больничному, поскольку СФР знал правильные данные о стаже работника
  > 18 сентября
  > Расторжение договора и передача незавершенных работ: суд не обязал подрядчика выставить счет-фактуру
  > 18 сентября
  > Строительство: срок применения прежних норм при экспертизе проектов предложено увеличить до 3 лет
  > 18 сентября
  > Новый расчет целевой субсидии на уплату налогов при оказании медпомощи – проект Минфина
  > 18 сентября
  > За отсутствие электронных перевозочных документов президент поручил временно не штрафовать
  > 18 сентября
  > Президент продлил специальные экономические меры в сфере импорта продуктов и сырья на 2 года
  > 18 сентября
  > Экстренное извещение об инфекции, отравлении или укусе животного: Минздрав утвердил новую форму
  > 18 сентября
  > Праздники и перенос выходных в 2027 году: правительство утвердило график
  > 18 сентября
  > Практика коллегии по экономическим спорам ВС РФ: обзор за август
  > 18 сентября
  > Обоснования бюджетных ассигнований, КВР и направления расходов: Минфин обновил таблицу на 2027 год
  > 18 сентября
  > Работодатель вовремя не сократил рабочую неделю инвалиду – суды обязали оплатить переработки
  > 18 сентября
  > Национальный режим при закупках ряда медизделий и средств связи предложено применять иначе
  > 17 сентября
  > Больничный исходя из МРОТ: суд вернул СФР переплату, когда работодатель не сообщил про 0,13 ставки
  > 17 сентября
  > Минэкономразвития предлагает у […] (9818 chars more)

## claim `41886053-7625-4110-b7da-bc2aa28ea25b`

- type: `temporal_fact`
- statement: Годовая инфляция в России по итогам августа 2026 года составила 6,33%.
- epistemic_status: `hypothesis` (head: current)
- grade: `E1`
- assessed_scope: `{"as_of": "2026-09-19", "scope_schema": "host-scope-v1", "source_domains": ["cbr.ru", "kommersant.ru"]}`
- freshness: `fresh` (as_of 2026-09-11T00:00:00+00:00)

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T15:44:06.051023+00:00", "scope_schema": "host-scope-v1", "source_domain": "kommersant.ru"}`
- source: https://www.kommersant.ru/doc/8953020 (content sha256 `b53d6a3fa9f14816…`) chunk `chunk-0`
- fragment:
  > Инфляция в России по итогам августа составила 6,33%
  > Реклама в «Ъ» www.kommersant.ru/ad
  > Реклама в «Ъ» www.kommersant.ru/ad
  > Коммерсантъ
  > Коммерсантъ FM
  > 
  >                         Вход
  >                     
  > 
  >                         Новый поиск.
  > 
  >                         Теперь с интеллектом
  >                     
  > Закрыть меню
  > 
  >                 Меню сайта
  >             
  > Закрыть
  > Газета
  > Weekend
  > Автопилот
  > Радио
  > Подписка
  > Регионы
  > Экономика
  > Политика
  > Мир
  > Бизнес
  > Финансы
  > Потребительский рынок
  > Телекоммуникации
  > Общество
  > Происшествия
  > Культура
  > Спорт
  > HyperТекст
  > ВЭФ-2026
  > Игры
  > Партнерские проекты
  > Review
  > Недвижимость
  > Инвестиции
  > Карьера
  > Технологии
  > Здоровье +
  > Сомелье
  > Ответственный бизнес
  > Юридический бизнес
  > Страна
  > Деньги
  > Наука
  > Стиль
  > Энциклопедия красоты
  > Приложения
  > Конференции
  > Клуб
  > Регата
  > Академия
  > Премия «КодЪ»
  > Банкротства
  > Картотека
  > Фотоагентство
  > Редакция
  > Реклама
  > Темы
  > Тенденции
  > Мультимедиа
  > Интервью
  > Справочники
  > Самое читаемое
  > Спецпроекты
  > E-mail рассылки
  > «Коммерсантъ» для Android
  > Скачать приложение
  > RuStore
  > AppGallery
  > Москва
  > Санкт-Петербург
  > Воронеж
  > Екатеринбург
  > Ижевск
  > Казань
  > Краснодар
  > Нижний Новгород
  > Новороссийск
  > Новосибирск
  > Пермь
  > Ростов-на-Дону
  > Самара
  > Саратов
  > Сочи
  > Ставрополь
  > Уфа
  > Челябинск
  > Ярославль
  > Предыдущая страница
  > $ 84,19
  > € 96,66
  > ¥ 12,58
  > IMOEX 2262,13
  > СВО
  > Что посмотреть в кино
  > Топ-1000 менеджеров
  > Санкции против России
  > Валютный прогноз
  > ИИ
  > Экономика РФ
  > РФ и США
  > Выборы-2026
  > БРИКС
  > Топливный кризис
  > Ближний Восток
  > Контуры будущего
  > Атаки беспилотников
  > Новые законы в сентябре
  > ВЭФ-2026
  > Генератор Медведева
  > Колесников о Путине
  > Подкаст Weekend
  > Тенденции
  > «Деньги»
  > Эксклюзивы «Ъ»
  > Названия операций
  > Тесты «Ъ»
  > Команда Трампа
  > «Ъ-Хронограф»
  > Наука
  > Книга об истории «Ъ»
  > Следующая страница
  > Экономика
  > 11.09.2026, 21:35
  > 
  >                 Росстат: годовая инфляция в августе ускорилась до 6,33%
  >             
  > Годовая инфляция в России по итогам августа составила 6,33% против 5,98% в июле, следует из 
  > данных
  >  Росстата. За месяц потребительские цены снизились на 0,08%. 
  > Продовольственные товары подешевели на 0,26%, плодоов […] (3440 chars more)

## claim `e75aa02b-2abd-4c66-a772-4f51cfa703ab`

- type: `temporal_fact`
- statement: Количество стран-членов Европейского союза составляет 27.
- epistemic_status: `hypothesis` (head: current)
- grade: `E1`
- assessed_scope: `{"as_of": "2024-01-01", "scope_schema": "host-scope-v1", "source_domains": ["wikipedia.org", "europa.eu"]}`
- freshness: `due` (as_of 2024-01-01T00:00:00+00:00)

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:10:12.560852+00:00", "scope_schema": "host-scope-v1", "source_domain": "europa.eu"}`
- source: https://european-union.europa.eu/principles-countries-history/facts-and-figures-european-union_en (content sha256 `d603e6ca5ed9eb86…`) chunk `chunk-0`
- fragment:
  > Key facts and figures | European Union
  > Skip to main content
  > English
  > Select your language
  > Close
  > EU official languages
  > bg
  > български
  > es
  > español
  > cs
  > čeština
  > da
  > dansk
  > de
  > Deutsch
  > et
  > eesti
  > el
  > ελληνικά
  > en
  > English
  > fr
  > français
  > ga
  > Gaeilge
  > hr
  > hrvatski
  > it
  > italiano
  > lv
  > latviešu
  > lt
  > lietuvių
  > hu
  > magyar
  > mt
  > Malti
  > nl
  > Nederlands
  > pl
  > polski
  > pt
  > português
  > ro
  > română
  > sk
  > slovenčina
  > sl
  > slovenščina
  > fi
  > suomi
  > sv
  > svenska
  > Other languages
  > ru
  > русский
  > uk
  > yкраїнська
  > Search
  > Search
  > European Union
  > Menu
  > Close
  > Menu
  > Back
  > Previous items
  > Next items
  > Home
  > Principles, countries, history
  > Principles and values
  > Facts and figures on the European Union 
  > EU countries
  > EU enlargement
  > Achievements
  > History of the EU
  > Europe Day
  > Symbols
  > Languages
  > See all
  > Institutions, law, budget
  > Institutions and bodies
  > Leadership 
  > Law
  > Budget
  > The Euro
  > See all
  > Priorities and actions
  > EU priorities
  > Actions by topic
  > EU support for Ukraine
  > See all
  > Live, work, study
  > Living in the EU
  > Travelling in the EU
  > Participate, interact, vote
  > Immigration to the EU
  > Working in the EU
  > Doing business in the EU
  > Import and export
  > Funding, grants, subsidies
  > Public contracts
  > Studying and training in the EU
  > Jobs and traineeships in EU institutions
  > See all
  > News and events
  > Featured news
  > Latest news from EU institutions and bodies
  > Events
  > Visual stories
  > See all
  > Contact the EU
  > Write to us
  > Call us
  > Meet us
  > Contact details: institutions, bodies and agencies
  > Visit a European Union institution
  > Europe and worldwide offices
  > Make a complaint
  > Press contacts
  > Social media channels
  > See all
  > Home
  > Principles, countries, history
  > Facts and figures on the European Union 
  > Facts and figures on the European Union 
  > Page contents
  > The EU and its Member States
  > People, size and open borders
  > Economy, trade and government finances
  > Energy and climate
  > Quality of life, jobs and equality
  > Tourism, languages and education
  > The EU and its Member States
  > Founded
  > : in 1951 
  > after the Second World War
  >  by six countries (Belgium, France, Germany, Italy, Luxembourg, and the Netherlands).
  > Current Member States
  > : 
  > 27 c […] (10335 chars more)

## claim `b56208f3-46b0-494f-a79c-b6ca49d011d1`

- type: `temporal_fact`
- statement: По состоянию на 13 августа 2026 года последней стабильной версией PostgreSQL является 18.6.
- epistemic_status: `hypothesis` (head: current)
- grade: `E1`
- assessed_scope: `{"as_of": "2026-09-19", "scope_schema": "host-scope-v1", "source_domains": ["postgresql.org", "habr.com"]}`
- freshness: `due` (as_of 2026-08-13T00:00:00+00:00)

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T15:09:50.149511+00:00", "scope_schema": "host-scope-v1", "source_domain": "postgresql.org"}`
- source: https://www.postgresql.org/ (content sha256 `ffb3cd4e0ffdaece…`) chunk `chunk-0`
- fragment:
  > PostgreSQL: The world's most advanced open source database
  > Home
  > About
  > Download
  > Documentation
  > Community
  > Developers
  > Support
  > Donate
  > Your account
  > August 13, 2026: 
  > PostgreSQL 18.6, 17.11, 16.15, 15.19, 14.24 and 19 Beta 3 Released!
  > PostgreSQL: The World's Most Advanced Open Source Relational Database
  > Download 
  > New to PostgreSQL?
  > New to PostgreSQL?
  > 
  >           PostgreSQL is a powerful, open source object-relational database system with over 35 years of active development
  >           that has earned it a strong reputation for reliability, feature robustness, and performance.
  >         
  > 
  >           There is a wealth of information to be found describing how to 
  > install
  >  and 
  > use
  >  PostgreSQL through the 
  > official documentation
  > .
  >           The 
  > open source community
  > 
  >           provides many helpful places to become familiar with PostgreSQL,
  >           discover how it works, and find career opportunities. Learn more on
  >           how to 
  > engage with the community
  > .
  >         
  > Learn More
  > Feature Matrix
  > Governance
  > Latest Releases
  > 2026-08-13 - 
  > 
  > PostgreSQL 19 Beta 3, 18.6, 17.11, 16.15, 15.19 and 14.24 Released!
  > 
  > 
  >           The PostgreSQL Global Development Group has
  >           
  > released an update
  >  to all supported versions
  >           of PostgreSQL, including
  >           
  > 19 Beta 3, 18.6, 17.11, 16.15, 15.19 and 14.24
  > .
  >           This release fixes 28 security vulnerabilities and 
  > 	  many bugs reported over the last several months.
  > 	
  > 
  >           For the more information about this release, please review the
  >           
  > release notes
  > . You can download
  >           PostgreSQL from the 
  > download
  >  page.
  > 	
  > 
  > PostgreSQL 14 will stop receiving fixes on November 12, 2026.
  > If you are running PostgreSQL 14 in a production environment, we suggest that
  > you make plans to upgrade to a newer, supported version of PostgreSQL. Please see our
  > 
  > versioning policy
  >  for more information.
  > 
  > 18.6
  >  · 2026-08-13 · 
  > Notes
  > 17.11
  >  · 2026-08-13 · 
  > Notes
  > 16.15
  >  · 2026-08-13 · 
  > Notes
  > 15.19
  >  · 2026-08-13 · 
  > Notes
  > 14.24
  >  · 2026-08- […] (4048 chars more)

## claim `424f071c-dbee-4c7c-ad0a-896a657ce12b`

- type: `temporal_fact`
- statement: Последние изменения в Конституцию Российской Федерации вступили в силу 4 июля 2020 года.
- epistemic_status: `hypothesis` (head: current)
- grade: `E1`
- assessed_scope: `{"as_of": "2020-07-04", "scope_schema": "host-scope-v1", "source_domains": ["wikipedia.org", "gov.ru"]}`
- freshness: `fresh` (as_of 2020-07-04T00:00:00+00:00)

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T16:12:25.120393+00:00", "scope_schema": "host-scope-v1", "source_domain": "wikipedia.org"}`
- source: https://ru.wikipedia.org/wiki/%D0%9A%D0%BE%D0%BD%D1%81%D1%82%D0%B8%D1%82%D1%83%D1%86%D0%B8%D1%8F_%D0%A0%D0%BE%D1%81%D1%81%D0%B8%D0%B9%D1%81%D0%BA%D0%BE%D0%B9_%D0%A4%D0%B5%D0%B4%D0%B5%D1%80%D0%B0%D1%86%D0%B8%D0%B8 (content sha256 `28e2ecd8fa27785e…`) chunk `chunk-0`
- fragment:
  > Конституция Российской Федерации — Википедия
  > Перейти к содержанию
  > Главное меню
  > Главное меню
  > переместить в боковую панель
  > скрыть
  > 
  > 		Навигация
  > 	
  > Заглавная страница
  > Содержание
  > Избранные статьи
  > Случайная статья
  > Текущие события
  > 
  > 		Участие
  > 	
  > Сообщить об ошибке
  > Как править статьи
  > Сообщество
  > Форум
  > Справка
  > Свежие правки
  > Новые страницы
  > Служебные страницы
  > 
  > 		На других языках
  > 	
  > العربية
  > Авар
  > Azərbaycanca
  > Башҡортса
  > Беларуская (тарашкевіца)
  > Беларуская
  > Български
  > বাংলা
  > Буряад
  > Нохчийн
  > Čeština
  > Чӑвашла
  > Cymraeg
  > Deutsch
  > Ελληνικά
  > English
  > Esperanto
  > Español
  > فارسی
  > Suomi
  > Français
  > Gaeilge
  > עברית
  > Magyar
  > Հայերեն
  > Bahasa Indonesia
  > Íslenska
  > Italiano
  > 日本語
  > 한국어
  > Latviešu
  > Монгол
  > Bahasa Melayu
  > Nederlands
  > Norsk bokmål
  > Polski
  > Português
  > Română
  > Саха тыла
  > Српски / srpski
  > Svenska
  > ไทย
  > Türkçe
  > Татарча / tatarça
  > Українська
  > اردو
  > Oʻzbekcha / ўзбекча
  > Tiếng Việt
  > 中文
  > Править ссылки
  > Поиск
  > Найти
  > Внешний вид
  > Пожертвовать
  > Создать учётную запись
  > Войти
  > Персональные инструменты
  > Пожертвовать
  > Создать учётную запись
  > Войти
  > Содержание
  > переместить в боковую панель
  > скрыть
  > Начало
  > 1
  > История конституции
  > Отобразить/Скрыть подраздел История конституции
  > 1.1
  > Разработка проекта
  > 1.2
  > Принятие (1993)
  > 2
  > Структура
  > 3
  > Конституционные поправки и пересмотр Конституции
  > Отобразить/Скрыть подраздел Конституционные поправки и пересмотр Конституции
  > 3.1
  > Внесение изменений в статью 65 Конституции в связи с изменением наименования субъекта России
  > 3.2
  > Внесение изменений в статью 65 Конституции в связи с изменением состава России
  > 3.3
  > Поправки к главам 3—8 Конституции
  > 3.4
  > Пересмотр положений глав 1, 2 и 9 Конституции
  > 3.5
  > Поправки 2020 года
  > 4
  > Отличия Конституции от законов
  > 5
  > Конституция и ограничение прав и свобод человека и гражданина
  > 6
  > Издания
  > 7
  > Переводы
  > 8
  > Фильмы о Конституции Российской Федерации
  > 9
  > Нумизматика
  > 10
  > Филателия
  > 11
  > См. также
  > 12
  > Примечания
  > 13
  > Литература
  > 14
  > Ссылки
  > Отобразить/Скрыть содержание
  > Конституция Российской Федерации
  > 49 языков
  > العربية
  > Авар
  > Azərbaycanca
  > Башҡортса
  > Беларуская (тарашкевіца)
  > Беларуская
  > Български
  > বাংলা
  > Буряад
  > Нохчийн
  > Čeština
  > Чӑвашла
  >  […] (43585 chars more)

## claim `dba77a4d-adef-4094-a2ee-988109618131`

- type: `temporal_fact`
- statement: Поддержка PostgreSQL 14 (End of Life) завершается 12 ноября 2026 года.
- epistemic_status: `hypothesis` (head: current)
- grade: `E1`
- assessed_scope: `{"as_of": "2026-11-12", "scope_schema": "host-scope-v1", "source_domains": ["postgresql.org", "habr.com"]}`
- freshness: `fresh` (as_of 2026-11-12T00:00:00+00:00)

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T15:52:07.766353+00:00", "scope_schema": "host-scope-v1", "source_domain": "postgresql.org"}`
- source: https://www.postgresql.org/ (content sha256 `e0ad98769f0e62a9…`) chunk `chunk-0`
- fragment:
  > PostgreSQL: The world's most advanced open source database
  > Home
  > About
  > Download
  > Documentation
  > Community
  > Developers
  > Support
  > Donate
  > Your account
  > August 13, 2026: 
  > PostgreSQL 18.6, 17.11, 16.15, 15.19, 14.24 and 19 Beta 3 Released!
  > PostgreSQL: The World's Most Advanced Open Source Relational Database
  > Download 
  > New to PostgreSQL?
  > New to PostgreSQL?
  > 
  >           PostgreSQL is a powerful, open source object-relational database system with over 35 years of active development
  >           that has earned it a strong reputation for reliability, feature robustness, and performance.
  >         
  > 
  >           There is a wealth of information to be found describing how to 
  > install
  >  and 
  > use
  >  PostgreSQL through the 
  > official documentation
  > .
  >           The 
  > open source community
  > 
  >           provides many helpful places to become familiar with PostgreSQL,
  >           discover how it works, and find career opportunities. Learn more on
  >           how to 
  > engage with the community
  > .
  >         
  > Learn More
  > Feature Matrix
  > Governance
  > Latest Releases
  > 2026-08-13 - 
  > 
  > PostgreSQL 19 Beta 3, 18.6, 17.11, 16.15, 15.19 and 14.24 Released!
  > 
  > 
  >           The PostgreSQL Global Development Group has
  >           
  > released an update
  >  to all supported versions
  >           of PostgreSQL, including
  >           
  > 19 Beta 3, 18.6, 17.11, 16.15, 15.19 and 14.24
  > .
  >           This release fixes 28 security vulnerabilities and 
  > 	  many bugs reported over the last several months.
  > 	
  > 
  >           For the more information about this release, please review the
  >           
  > release notes
  > . You can download
  >           PostgreSQL from the 
  > download
  >  page.
  > 	
  > 
  > PostgreSQL 14 will stop receiving fixes on November 12, 2026.
  > If you are running PostgreSQL 14 in a production environment, we suggest that
  > you make plans to upgrade to a newer, supported version of PostgreSQL. Please see our
  > 
  > versioning policy
  >  for more information.
  > 
  > 18.6
  >  · 2026-08-13 · 
  > Notes
  > 17.11
  >  · 2026-08-13 · 
  > Notes
  > 16.15
  >  · 2026-08-13 · 
  > Notes
  > 15.19
  >  · 2026-08-13 · 
  > Notes
  > 14.24
  >  · 2026-08- […] (4048 chars more)

## claim `700e4c0f-ada6-4217-b4c7-dd7145119b8d`

- type: `temporal_fact`
- statement: Официальный курс доллара США (USD) по отношению к рублю (RUB), установленный Банком России, составляет 84,1975 ₽ за 1 USD по состоянию на 19 сентября 2026 года.
- epistemic_status: `hypothesis` (head: current)
- grade: `E1`
- assessed_scope: `{"as_of": "2026-09-19", "scope_schema": "host-scope-v1", "source_domains": ["cbr.ru", "banki.ru"]}`
- freshness: `fresh` (as_of 2026-09-19T00:00:00+00:00)

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:18:01.448365+00:00", "scope_schema": "host-scope-v1", "source_domain": "cbr.ru"}`
- source: https://cbr.ru/ (content sha256 `67633e5fe27c4928…`) chunk `chunk-0`
- fragment:
  > Центральный банк Российской Федерации | Банк России
  > EN
  > Поиск по сайту
  > EN
  > Поиск по сайту
  > О Банке России
  > Деятельность
  > Денежно-кредитная политика
  > Финансовая стабильность
  > Национальная платежная система
  > Наличное денежное обращение
  > Развитие финансового рынка
  > Развитие финансовых технологий
  > Защита прав потребителей финансовых услуг
  > Информационная безопасность
  > Противодействие недобросовестным практикам
  > Противодействие отмыванию денег и валютный контроль
  > Допуск на финансовый рынок
  > Деловая репутация
  > Исследования
  > Операции Банка России
  > Финансовые рынки
  > Банковский сектор
  > Пенсионные фонды и коллективные инвестиции
  > Страхование
  > Рынок ценных бумаг
  > Эмитенты и корпоративное управление
  > Микрофинансирование
  > Инфраструктура финансового рынка
  > Кредитные истории
  > Сервисы
  > Обратиться в Банк России
  > Проверить участника финансового рынка
  > Список компаний с выявленными признаками нелегальной деятельности на финансовом рынке
  > Проверка уровня риска на платформе «Знай своего клиента»
  > Вопросы и ответы
  > Личный кабинет участника информационного обмена
  > Информация о кредитных рейтингах
  > Конструктор оценки деловой репутации и квалификации
  > Требования и рекомендации к сайтам финансовых организаций
  > Разъяснения
  > Удостоверяющий центр Банка России
  > Технические ресурсы
  > Осторожно: мошенники!
  > Что вы хотите найти?
  > Искать
  > Деятельность
  > Финансовые рынки
  > Документы и данные
  > О Банке России
  > Сервисы
  > Меры защиты финансового рынка
  > +
  > 7 499 300-30-00
  > 8 800 300-30-00
  > 300
  > Бесплатно для звонков с мобильных телефонов
  > Новости
  > Решения Банка России
  > Контактная информация
  > Карта сайта
  > О сайте
  > Обратиться в Банк России
  > RU
  > EN
  > О Банке России
  > Проверить участника финансового рынка
  > Осторожно: мошенники!
  > Центральный банк 
  >  Российской Федерации
  > Обеспечиваем ценовую и финансовую стабильность, создаем условия для устойчивого роста экономики
  > Открытый урок 
  > Зульфии Кахрумановой
  > Вселенная цифрового рубля: 
  > все о новой форме денег
  > 22 сентября, 9:30
  > Цифровой рубль
  > Все о новой форме национальной валюты
  > № 31 (2620)
  > Вестник 
  > Банка России
  > от 16 сентября 2026 года
  > Це […] (2556 chars more)

## claim `50856fc7-c6c3-440a-94a6-99733c6a502d`

- type: `temporal_fact`
- statement: На момент 15 апреля 2026 года последней стабильной версией Python, доступной для загрузки на https://www.python.org/downloads/, является 3.14.7.
- epistemic_status: `hypothesis` (head: current)
- grade: `E1`
- assessed_scope: `{"as_of": "2026-09-19", "scope_schema": "host-scope-v1", "source_domains": ["python.org", "chocolatey.org"]}`
- freshness: `due` (as_of 2026-04-15T00:00:00+00:00)

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:07:02.892676+00:00", "scope_schema": "host-scope-v1", "source_domain": "python.org"}`
- source: https://www.python.org/downloads/ (content sha256 `52ea3a5a1ee755bb…`) chunk `chunk-0`
- fragment:
  > Download Python | Python.org
  > Notice:
  >  This page displays a fallback because interactive scripts did not run. Possible causes include disabled JavaScript or failure to load scripts or stylesheets.
  > Skip to content
  > ▼
  >  Close
  >                 
  > Python
  > PSF
  > Docs
  > PyPI
  > Jobs
  > Community
  > ▲
  >  The Python Network
  >                 
  > Donate
  > ≡
  >  Menu
  > Search This Site
  > 
  >                                     GO
  >                                 
  > A
  >  A
  > Smaller
  > Larger
  > Reset
  > Socialize
  > LinkedIn
  > Mastodon
  > Chat on IRC
  > Twitter
  > About
  > Applications
  > Quotes
  > Getting Started
  > Help
  > Downloads
  > All releases
  > Source code
  > Windows
  > macOS
  > Android
  > iOS
  > Other Platforms
  > License
  > Alternative Implementations
  > Documentation
  > Docs
  > Audio/Visual Talks
  > Beginner's Guide
  > FAQ
  > Non-English Docs
  > PEP Index
  > Python Books
  > Python Essays
  > Community
  > Diversity
  > Mailing Lists
  > IRC
  > Forums
  > PSF Annual Impact Report
  > Python Conferences
  > Special Interest Groups
  > Python Logo
  > Python Wiki
  > Code of Conduct
  > Community Awards
  > Get Involved
  > Shared Stories
  > Success Stories
  > Arts
  > Business
  > Education
  > Engineering
  > Government
  > Scientific
  > Software Development
  > News
  > Python News
  > PSF Newsletter
  > PSF News
  > PyCon US News
  > Python Insider
  > News from the Community
  > Events
  > Python Events
  > User Group Events
  > Python Events Archive
  > User Group Events Archive
  > Submit an Event
  > Download the latest version for Android
  > Download the latest source release
  > Download Python 3.14.7
  > Download the latest version for Windows
  > Download Python install manager
  > Or get the standalone installer for 
  > Python 3.14.7
  > Download the latest version for iOS
  > Download the latest version for macOS
  > Download Python 3.14.7
  > Download the latest version of Python
  > Download Python 3.14.7
  > 
  >   Looking for Python with a different OS? Python for
  >   
  > Windows
  > ,
  >   
  > Linux/Unix
  > ,
  >   
  > macOS
  > ,
  >   
  > Android
  > ,
  >   
  > iOS
  > ,
  >   
  > other
  > 
  >   Want to help test development versions of Python 3.15?
  >   
  > Pre-releases
  > ,
  >   
  > Docker images
  > Active Python releases
  > For more information visit the Python Developer's Guide
  > .
  > Python version
  > Maintenance status
  > First released
  > End of s […] (19255 chars more)

## claim `aa2626c5-816e-4d9f-aecc-e90bce35336c`

- type: `temporal_fact`
- statement: Количество государств-членов ООН составляет 193.
- epistemic_status: `hypothesis` (head: current)
- grade: `E1`
- assessed_scope: `{"as_of": "2026-09-19", "scope_schema": "host-scope-v1", "source_domains": ["un.org", "wikipedia.org"]}`
- freshness: `due` (as_of 2024-05-22T00:00:00+00:00)

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:43:05.948275+00:00", "scope_schema": "host-scope-v1", "source_domain": "un.org"}`
- source: https://www.un.org/en/about-us (content sha256 `4142fcedbc857430…`) chunk `chunk-0`
- fragment:
  > About Us | United Nations
  > Skip to main content
  > Toggle navigation
  > Welcome to the United Nations
  > العربية
  > 中文
  > Nederlands
  > English
  > Français
  > Deutsch
  > Kreyòl
  > हिन्दी
  > Bahasa Indonesia
  > Italiano
  > Polski
  > Português
  > Русский
  > Español
  > Kiswahili
  > Türkçe
  > Українська
  > Peace, dignity and equality 
  > on a healthy planet
  > Search the United Nations
  > Submit Search
  > A-Z Site Index
  > Toggle navigation
  > About Us          
  >  »
  > About Us
  > Member States
  > Main Bodies
  > Secretary-General
  > Secretariat
  > UN System
  > History
  > Emblem and Flag
  > UN Charter
  > UDHR
  > ICJ Statute
  > Nobel Peace Prize
  > Our Work          
  >  »
  > Our Work
  > Peace and Security
  > Human Rights
  > Humanitarian Aid
  > Sustainable Development and Climate
  > International Law
  > Global Issues
  > Documents
  > Official Languages
  > Observances
  > Events and News          
  > Get Involved          
  > General Debate          
  > The UN Pulse          
  > About Us
  > One place where the world's nations can
  > gather
  >  together, 
  > discuss
  >  common problems
  > 
  > 			and 
  > find shared solutions
  > .
  > The United Nations is an international organization founded in 1945. Currently made up of 193 
  > Member States
  > , the 
  > UN and its work
  >  are guided by the purposes and principles contained in its founding 
  > Charter
  > .
  > The UN has evolved over the years to keep pace with a rapidly changing world.
  > But one thing has stayed the same: it remains the one place on Earth where all the world’s nations can gather together, discuss common problems, and find shared solutions that benefit all of humanity.
  > The most impossible job on Earth. So far only 9 people have held the top leadership position in the world’s largest multilateral organization. What does it mean to be 
  > UN Secretary-General
  > ?
  > Member
  > 
  > 			States
  > The UN’s Membership has 
  > grown from the original 51 Member States
  >  in 1945 to the 
  > current 193 Member States
  > .
  > All UN Member States are members of the 
  > General Assembly
  > .  States are admitted to membership by a decision of the General Assembly upon the recommendation of the 
  > Security Council
  > .
  > Secretary-General
  > In the end, it comes down to values [...] W […] (5396 chars more)

## claim `5b22871b-60c3-416f-8aac-545752570861`

- type: `temporal_fact`
- statement: Официальный курс Банка России составляет 53,5948 ₽ за 100 японских иен по состоянию на 19 сентября 2026 года.
- epistemic_status: `hypothesis` (head: current)
- grade: `E1`
- assessed_scope: `{"as_of": "2026-09-19", "scope_schema": "host-scope-v1", "source_domains": ["cbr.ru", "banki.ru"]}`
- freshness: `fresh` (as_of 2026-09-19T00:00:00+00:00)

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T16:23:02.575778+00:00", "scope_schema": "host-scope-v1", "source_domain": "cbr.ru"}`
- source: https://www.cbr.ru/eng/currency_base/daily/ (content sha256 `b2b3d7fafe5d64f9…`) chunk `chunk-0`
- fragment:
  > Official exchange rates on selected date | Bank of Russia
  > 12 Neglinnaya Street, Moscow, 107016 Russia
  > 8 800 300-30-00
  > www.cbr.ru
  > RU
  > Search
  > RU
  > Search
  > What do you want to find?
  > Search
  > Activity
  > Financial markets
  > Documents and data
  > About Bank of Russia
  > Services
  > +
  > 7 499 300-30-00
  > 8 800 300-30-00
  > Events
  > Contacts
  > Site map
  > About the Site 
  > About Bank of Russia
  > RU
  > EN
  > Databases
  > Foreign Currency Market
  > Official exchange rates on selected date
  > 19.09.2026
  > 
  > 		  The Central Bank of the Russian Federation has set from  19.09.2026 the following exchange rates of foreign currencies against the ruble  without assuming any liability to buy or sell foreign currency at the rates below		
  > 		
  > Num сode
  > Char сode
  > Unit
  > Currency
  > Rate
  > 036
  > AUD
  > 1
  > Australian Dollar
  > 59.9991
  > 944
  > AZN
  > 1
  > Azerbaijan Manat
  > 49.5279
  > 012
  > DZD
  > 100
  > Algerian Dinar
  > 63.0011
  > 051
  > AMD
  > 100
  > Armenian Dram
  > 23.1668
  > 764
  > THB
  > 10
  > Baht
  > 25.3058
  > 048
  > BHD
  > 1
  > Bahraini Dinar
  > 223.8812
  > 933
  > BYN
  > 1
  > Belarusian Ruble
  > 27.8486
  > 068
  > BOB
  > 10
  > Bolivian Boliviano
  > 84.1134
  > 986
  > BRL
  > 1
  > Brazilian Real
  > 16.3433
  > 410
  > KRW
  > 1000
  > Won
  > 60.9994
  > 344
  > HKD
  > 1
  > Hong Kong Dollar
  > 10.7313
  > 980
  > UAH
  > 10
  > Hryvnia
  > 18.8470
  > 208
  > DKK
  > 1
  > Danish Krone
  > 12.9316
  > 784
  > AED
  > 1
  > UAE Dirham
  > 22.9265
  > 840
  > USD
  > 1
  > US Dollar
  > 84.1975
  > 704
  > VND
  > 10000
  > Dong
  > 32.8422
  > 978
  > EUR
  > 1
  > Euro
  > 96.6671
  > 818
  > EGP
  > 10
  > Egyptian Pound
  > 16.1465
  > 985
  > PLN
  > 1
  > Zloty
  > 22.1584
  > 392
  > JPY
  > 100
  > Yen
  > 53.5948
  > 356
  > INR
  > 100
  > Indian Rupee
  > 87.8971
  > 364
  > IRR
  > 1000000
  > Iranian Rial
  > 50.9878
  > 124
  > CAD
  > 1
  > Canadian Dollar
  > 60.1927
  > 634
  > QAR
  > 1
  > Qatari Rial
  > 23.1312
  > 192
  > CUP
  > 10
  > Cuban peso
  > 35.0823
  > 104
  > MMK
  > 1000
  > Kyat
  > 40.0940
  > 981
  > GEL
  > 1
  > Lari
  > 32.2893
  > 498
  > MDL
  > 10
  > Moldovan Leu
  > 48.0453
  > 566
  > NGN
  > 1000
  > Nigeria Naira
  > 63.2455
  > 554
  > NZD
  > 1
  > New Zealand Dollar
  > 48.1736
  > 934
  > TMT
  > 1
  > Turkmenistan New Manat
  > 24.0564
  > 578
  > NOK
  > 10
  > Norwegian Krone
  > 89.3001
  > 512
  > OMR
  > 1
  > Omani Rial
  > 218.9792
  > 946
  > RON
  > 1
  > Romanian Leu
  > 18.3681
  > 360
  > IDR
  > 10000
  > Rupiah
  > 47.4272
  > 710
  > ZAR
  > 10
  > Rand
  > 51.7712
  > 682
  > SAR
  > 1
  > Saudi Riyal
  > 22.4527
  > 960
  > XDR
  > 1
  > SDR (Special Drawing Right)
  > 114.9927
  > 941
  > RSD
  > 100
  > Serbian Dinar
  > 82.3825
  > 702
  > SGD
  > 1
  > Singapore Dollar
  > 66.0114
  > 417
  > KGS
  > 100
  > Som
  > 96.2 […] (984 chars more)

## claim `2c372922-9a70-4afb-9c71-3a06ea575989`

- type: `temporal_fact`
- statement: Численность населения Земли на 2026 год составляет 8,3 миллиарда человек.
- epistemic_status: `hypothesis` (head: current)
- grade: `E1`
- assessed_scope: `{"as_of": "2026-09-19", "scope_schema": "host-scope-v1", "source_domains": ["worldometers.info", "wikipedia.org"]}`
- freshness: `due` (as_of 2026-01-01T00:00:00+00:00)

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T13:36:57.840245+00:00", "scope_schema": "host-scope-v1", "source_domain": "worldometers.info"}`
- source: https://www.worldometers.info/world-population/ (content sha256 `1bba9f6b306f795f…`) chunk `chunk-0`
- fragment:
  > World Population Clock: 8.3 Billion People (LIVE, 2026) - Worldometer
  > Population
  > More
  >  CO2 emissions 
  >   Coronavirus  
  >  Countries 
  >  Energy 
  >  Flags 
  >  Food & Agriculture 
  >  GDP by country 
  >   Time  
  >   Unit Converters  
  >  Water 
  >   World Map  
  > English
  >  Bahasa Indonesia 
  >  Català 
  >  Čeština 
  >  Dansk 
  >  Deutsch 
  >  Eesti 
  >  English 
  >  Español 
  >  Français 
  >  Hrvatski 
  >  Italiano 
  >  Magyar 
  >  Nederlands 
  >  Norsk 
  >  Polski 
  >  Português 
  >  Română 
  >  Suomi 
  >  Svenska 
  >  Türkçe 
  >  Ελληνικά 
  >  Русский 
  >  Українська 
  >  العربية 
  >  中文 
  >  日本語 
  >  Population 
  >  CO2 emissions 
  >   Coronavirus  
  >  Countries 
  >  Energy 
  >  Flags 
  >  Food & Agriculture 
  >  GDP by country 
  >   Time  
  >   Unit Converters  
  >  Water 
  >   World Map  
  > W
  > /
  > Population
  > /
  >  World Population 
  >  Current World Population 
  > retrieving data...
  > view all people on 1 page >
  >   Today  
  > Births today
  > retrieving data...
  > Deaths today
  > retrieving data...
  > Population Growth today
  > retrieving data...
  >   This Year  
  > Births this year
  > retrieving data...
  > Deaths this year
  > retrieving data...
  > Population Growth this year
  > retrieving data...
  >  World Population Sections 
  > Top 20 Countries LIVE
  > Past, present, and future
  > Global Growth Rate
  > Historical data
  > Forecast
  > Milestones
  > by Region
  > by Religion
  > Population Density
  > Population by Country
  > All-time total
  > Sources and methods
  > Demographics
  > : 
  > World Demographics
  >  | 
  > Age Structure
  >  | 
  > Sex Ratio
  >  | 
  > Population Pyramid
  >  | 
  > Median Age
  >  | 
  > Fertility Rate
  >  | 
  > Life Expectancy
  >  | 
  > Urbanization
  >  Top 20 Largest Countries by Population (live) 
  > 1
  >  India 
  > retrieving data...
  > 2
  >  China 
  > retrieving data...
  > 3
  >  U.S.A. 
  > retrieving data...
  > 4
  >  Indonesia 
  > retrieving data...
  > 5
  >  Pakistan 
  > retrieving data...
  > 6
  >  Nigeria 
  > retrieving data...
  > 7
  >  Brazil 
  > retrieving data...
  > 8
  >  Bangladesh 
  > retrieving data...
  > 9
  >  Russia 
  > retrieving data...
  > 10
  >  Ethiopia 
  > retrieving data...
  > 11
  >  Mexico 
  > retrieving data...
  > 12
  >  Japan 
  > retrieving data...
  > 13
  >  Egypt 
  > retrieving data...
  > 14
  >  Philippines 
  > retrieving data...
  > 15
  >  DR Congo 
  > retrieving data...
  > 16
  >  Vietnam 
  > retrieving data...
  > 17
  >  Iran 
  > retrieving data...
  > 18
  >   […] (37731 chars more)

## claim `5df8c746-fd6a-462b-b247-f6a34ca125ea`

- type: `external_fact`
- statement: Европейский союз имеет 24 официальных языка.
- epistemic_status: `hypothesis` (head: current)
- grade: `E1`
- assessed_scope: `{"as_of": "2026-09-19", "scope_schema": "host-scope-v1", "source_domains": ["europa.eu", "wikipedia.org"]}`
- freshness: `fresh`

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T15:46:23.279232+00:00", "scope_schema": "host-scope-v1", "source_domain": "europa.eu"}`
- source: https://european-union.europa.eu/index_en (content sha256 `2a3dc0ceb42f23c5…`) chunk `chunk-0`
- fragment:
  > Your gateway to the EU, News, Highlights | European Union
  > Skip to main content
  > English
  > Select your language
  > Close
  > EU official languages
  > bg
  > български
  > es
  > español
  > cs
  > čeština
  > da
  > dansk
  > de
  > Deutsch
  > et
  > eesti
  > el
  > ελληνικά
  > en
  > English
  > fr
  > français
  > ga
  > Gaeilge
  > hr
  > hrvatski
  > it
  > italiano
  > lv
  > latviešu
  > lt
  > lietuvių
  > hu
  > magyar
  > mt
  > Malti
  > nl
  > Nederlands
  > pl
  > polski
  > pt
  > português
  > ro
  > română
  > sk
  > slovenčina
  > sl
  > slovenščina
  > fi
  > suomi
  > sv
  > svenska
  > Other languages
  > ru
  > русский
  > uk
  > yкраїнська
  > Search
  > Search
  > European Union
  > Menu
  > Close
  > Menu
  > Back
  > Previous items
  > Next items
  > Home
  > Principles, countries, history
  > Principles and values
  > Facts and figures on the European Union 
  > EU countries
  > EU enlargement
  > Achievements
  > History of the EU
  > Europe Day
  > Symbols
  > Languages
  > See all
  > Institutions, law, budget
  > Institutions and bodies
  > Leadership 
  > Law
  > Budget
  > The Euro
  > See all
  > Priorities and actions
  > EU priorities
  > Actions by topic
  > EU support for Ukraine
  > See all
  > Live, work, study
  > Living in the EU
  > Travelling in the EU
  > Participate, interact, vote
  > Immigration to the EU
  > Working in the EU
  > Doing business in the EU
  > Import and export
  > Funding, grants, subsidies
  > Public contracts
  > Studying and training in the EU
  > Jobs and traineeships in EU institutions
  > See all
  > News and events
  > Featured news
  > Latest news from EU institutions and bodies
  > Events
  > Visual stories
  > See all
  > Contact the EU
  > Write to us
  > Call us
  > Meet us
  > Contact details: institutions, bodies and agencies
  > Visit a European Union institution
  > Europe and worldwide offices
  > Make a complaint
  > Press contacts
  > Social media channels
  > See all
  > Your gateway to the European Union
  > Explore the EU
  > History of the EU
  > EU countries
  > Institutions and bodies
  > Featured news
  > All featured news are available in the 24 EU official languages via machine translation.
  > News article
  > 17 September 2026
  > EU KIDS Act: helping children navigate a safer online world
  > In the face of growing concerns about the risks children and teenagers face when using online platforms, the EU has proposed a new KIDS Act. This will introduce a gradual uptake of social m […] (2325 chars more)

## claim `0c3c2ebf-208e-463e-9503-01f767edcade`

- type: `external_fact`
- statement: Численность населения Земли на 2026 год составляет 8,3 миллиарда человек.
- epistemic_status: `hypothesis` (head: current)
- grade: `E1`
- assessed_scope: `{"as_of": "2026-09-19", "scope_schema": "host-scope-v1", "source_domains": ["worldometers.info", "wikipedia.org"]}`
- freshness: `fresh`

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T14:17:14.326056+00:00", "scope_schema": "host-scope-v1", "source_domain": "worldometers.info"}`
- source: https://www.worldometers.info/world-population/ (content sha256 `23e8606e522fd917…`) chunk `chunk-0`
- fragment:
  > World Population Clock: 8.3 Billion People (LIVE, 2026) - Worldometer
  > Population
  > More
  >  CO2 emissions 
  >   Coronavirus  
  >  Countries 
  >  Energy 
  >  Flags 
  >  Food & Agriculture 
  >  GDP by country 
  >   Time  
  >   Unit Converters  
  >  Water 
  >   World Map  
  > English
  >  Bahasa Indonesia 
  >  Català 
  >  Čeština 
  >  Dansk 
  >  Deutsch 
  >  Eesti 
  >  English 
  >  Español 
  >  Français 
  >  Hrvatski 
  >  Italiano 
  >  Magyar 
  >  Nederlands 
  >  Norsk 
  >  Polski 
  >  Português 
  >  Română 
  >  Suomi 
  >  Svenska 
  >  Türkçe 
  >  Ελληνικά 
  >  Русский 
  >  Українська 
  >  العربية 
  >  中文 
  >  日本語 
  >  Population 
  >  CO2 emissions 
  >   Coronavirus  
  >  Countries 
  >  Energy 
  >  Flags 
  >  Food & Agriculture 
  >  GDP by country 
  >   Time  
  >   Unit Converters  
  >  Water 
  >   World Map  
  > W
  > /
  > Population
  > /
  >  World Population 
  >  Current World Population 
  > retrieving data...
  > view all people on 1 page >
  >   Today  
  > Births today
  > retrieving data...
  > Deaths today
  > retrieving data...
  > Population Growth today
  > retrieving data...
  >   This Year  
  > Births this year
  > retrieving data...
  > Deaths this year
  > retrieving data...
  > Population Growth this year
  > retrieving data...
  >  World Population Sections 
  > Top 20 Countries LIVE
  > Past, present, and future
  > Global Growth Rate
  > Historical data
  > Forecast
  > Milestones
  > by Region
  > by Religion
  > Population Density
  > Population by Country
  > All-time total
  > Sources and methods
  > Demographics
  > : 
  > World Demographics
  >  | 
  > Age Structure
  >  | 
  > Sex Ratio
  >  | 
  > Population Pyramid
  >  | 
  > Median Age
  >  | 
  > Fertility Rate
  >  | 
  > Life Expectancy
  >  | 
  > Urbanization
  >  Top 20 Largest Countries by Population (live) 
  > 1
  >  India 
  > retrieving data...
  > 2
  >  China 
  > retrieving data...
  > 3
  >  U.S.A. 
  > retrieving data...
  > 4
  >  Indonesia 
  > retrieving data...
  > 5
  >  Pakistan 
  > retrieving data...
  > 6
  >  Nigeria 
  > retrieving data...
  > 7
  >  Brazil 
  > retrieving data...
  > 8
  >  Bangladesh 
  > retrieving data...
  > 9
  >  Russia 
  > retrieving data...
  > 10
  >  Ethiopia 
  > retrieving data...
  > 11
  >  Mexico 
  > retrieving data...
  > 12
  >  Japan 
  > retrieving data...
  > 13
  >  Egypt 
  > retrieving data...
  > 14
  >  Philippines 
  > retrieving data...
  > 15
  >  DR Congo 
  > retrieving data...
  > 16
  >  Vietnam 
  > retrieving data...
  > 17
  >  Iran 
  > retrieving data...
  > 18
  >   […] (37731 chars more)

### evidence 2: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T14:40:53.497932+00:00", "scope_schema": "host-scope-v1", "source_domain": "worldometers.info"}`
- source: https://www.worldometers.info/world-population/ (content sha256 `94ed22c6c435b79a…`) chunk `chunk-0`
- fragment:
  > World Population Clock: 8.3 Billion People (LIVE, 2026) - Worldometer
  > Population
  > More
  >  CO2 emissions 
  >   Coronavirus  
  >  Countries 
  >  Energy 
  >  Flags 
  >  Food & Agriculture 
  >  GDP by country 
  >   Time  
  >   Unit Converters  
  >  Water 
  >   World Map  
  > English
  >  Bahasa Indonesia 
  >  Català 
  >  Čeština 
  >  Dansk 
  >  Deutsch 
  >  Eesti 
  >  English 
  >  Español 
  >  Français 
  >  Hrvatski 
  >  Italiano 
  >  Magyar 
  >  Nederlands 
  >  Norsk 
  >  Polski 
  >  Português 
  >  Română 
  >  Suomi 
  >  Svenska 
  >  Türkçe 
  >  Ελληνικά 
  >  Русский 
  >  Українська 
  >  العربية 
  >  中文 
  >  日本語 
  >  Population 
  >  CO2 emissions 
  >   Coronavirus  
  >  Countries 
  >  Energy 
  >  Flags 
  >  Food & Agriculture 
  >  GDP by country 
  >   Time  
  >   Unit Converters  
  >  Water 
  >   World Map  
  > W
  > /
  > Population
  > /
  >  World Population 
  >  Current World Population 
  > retrieving data...
  > view all people on 1 page >
  >   Today  
  > Births today
  > retrieving data...
  > Deaths today
  > retrieving data...
  > Population Growth today
  > retrieving data...
  >   This Year  
  > Births this year
  > retrieving data...
  > Deaths this year
  > retrieving data...
  > Population Growth this year
  > retrieving data...
  >  World Population Sections 
  > Top 20 Countries LIVE
  > Past, present, and future
  > Global Growth Rate
  > Historical data
  > Forecast
  > Milestones
  > by Region
  > by Religion
  > Population Density
  > Population by Country
  > All-time total
  > Sources and methods
  > Demographics
  > : 
  > World Demographics
  >  | 
  > Age Structure
  >  | 
  > Sex Ratio
  >  | 
  > Population Pyramid
  >  | 
  > Median Age
  >  | 
  > Fertility Rate
  >  | 
  > Life Expectancy
  >  | 
  > Urbanization
  >  Top 20 Largest Countries by Population (live) 
  > 1
  >  India 
  > retrieving data...
  > 2
  >  China 
  > retrieving data...
  > 3
  >  U.S.A. 
  > retrieving data...
  > 4
  >  Indonesia 
  > retrieving data...
  > 5
  >  Pakistan 
  > retrieving data...
  > 6
  >  Nigeria 
  > retrieving data...
  > 7
  >  Brazil 
  > retrieving data...
  > 8
  >  Bangladesh 
  > retrieving data...
  > 9
  >  Russia 
  > retrieving data...
  > 10
  >  Ethiopia 
  > retrieving data...
  > 11
  >  Mexico 
  > retrieving data...
  > 12
  >  Japan 
  > retrieving data...
  > 13
  >  Egypt 
  > retrieving data...
  > 14
  >  Philippines 
  > retrieving data...
  > 15
  >  DR Congo 
  > retrieving data...
  > 16
  >  Vietnam 
  > retrieving data...
  > 17
  >  Iran 
  > retrieving data...
  > 18
  >   […] (37731 chars more)

## claim `299b5a8c-13fa-4153-a011-9d26c55f78aa`

- type: `external_fact`
- statement: Антониу Гутерриш является 9-м Генеральным секретарём ООН, занимающим данный пост с 1 января 2017 года.
- epistemic_status: `hypothesis` (head: current)
- grade: `E1`
- assessed_scope: `{"as_of": "2026-09-19", "scope_schema": "host-scope-v1", "source_domains": ["un.org", "wikipedia.org"]}`
- freshness: `fresh`

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T16:15:51.097060+00:00", "scope_schema": "host-scope-v1", "source_domain": "wikipedia.org"}`
- source: https://ru.wikipedia.org/wiki/%D0%90%D0%BD%D1%82%D0%BE%D0%BD%D0%B8%D1%83_%D0%93%D1%83%D1%82%D0%B5%D1%80%D1%80%D0%B8%D1%88 (content sha256 `d41f83c1046488e7…`) chunk `chunk-0`
- fragment:
  > Гутерриш, Антониу — Википедия
  > Перейти к содержанию
  > Главное меню
  > Главное меню
  > переместить в боковую панель
  > скрыть
  > 
  > 		Навигация
  > 	
  > Заглавная страница
  > Содержание
  > Избранные статьи
  > Случайная статья
  > Текущие события
  > 
  > 		Участие
  > 	
  > Сообщить об ошибке
  > Как править статьи
  > Сообщество
  > Форум
  > Справка
  > Свежие правки
  > Новые страницы
  > Служебные страницы
  > 
  > 		На других языках
  > 	
  > Afrikaans
  > Alemannisch
  > Aragonés
  > العربية
  > مصرى
  > অসমীয়া
  > Asturianu
  > Azərbaycanca
  > تۆرکجه
  > Башҡортса
  > Беларуская
  > Betawi
  > Български
  > বাংলা
  > Brezhoneg
  > Bosanski
  > Català
  > کوردی
  > Čeština
  > Cymraeg
  > Dansk
  > Deutsch
  > Ελληνικά
  > English
  > Esperanto
  > Español
  > Eesti
  > Euskara
  > فارسی
  > Suomi
  > Français
  > Arpetan
  > Nordfriisk
  > Galego
  > Hausa
  > עברית
  > हिन्दी
  > Hrvatski
  > Hornjoserbsce
  > Magyar
  > Հայերեն
  > Արեւմտահայերէն
  > Bahasa Indonesia
  > Interlingue
  > Ido
  > Íslenska
  > Italiano
  > 日本語
  > Jawa
  > ქართული
  > Қазақша
  > ភាសាខ្មែរ
  > 한국어
  > Kurdî
  > Кыргызча
  > Latina
  > Lëtzebuergesch
  > Lombard
  > Lietuvių
  > Latviešu
  > मैथिली
  > Malagasy
  > മലയാളം
  > Монгол
  > मराठी
  > Bahasa Melayu
  > Mirandés
  > မြန်မာဘာသာ
  > مازِرونی
  > Plattdüütsch
  > नेपाली
  > Nederlands
  > Norsk bokmål
  > Occitan
  > ਪੰਜਾਬੀ
  > Polski
  > Piemontèis
  > Português
  > Runa Simi
  > Română
  > ᱥᱟᱱᱛᱟᱲᱤ
  > Srpskohrvatski / српскохрватски
  > တႆး
  > සිංහල
  > Simple English
  > Slovenčina
  > Slovenščina
  > Soomaaliga
  > Shqip
  > Српски / srpski
  > Svenska
  > Kiswahili
  > தமிழ்
  > తెలుగు
  > Тоҷикӣ
  > ไทย
  > Türkçe
  > Татарча / tatarça
  > ئۇيغۇرچە / Uyghurche
  > Українська
  > اردو
  > Oʻzbekcha / ўзбекча
  > Tiếng Việt
  > Winaray
  > 吴语
  > მარგალური
  > 閩南語 / Bân-lâm-gí
  > 粵語
  > 中文
  > Править ссылки
  > Поиск
  > Найти
  > Внешний вид
  > Пожертвовать
  > Создать учётную запись
  > Войти
  > Персональные инструменты
  > Пожертвовать
  > Создать учётную запись
  > Войти
  > Содержание
  > переместить в боковую панель
  > скрыть
  > Начало
  > 1
  > Биография
  > Отобразить/Скрыть подраздел Биография
  > 1.1
  > Карьера в Социалистической партии
  > 1.2
  > Премьер-министр Португалии
  > 1.3
  > В международных организациях
  > 2
  > Личная жизнь
  > 3
  > Награды
  > 4
  > Примечания
  > 5
  > Литература
  > Отобразить/Скрыть содержание
  > Гутерриш, Антониу
  > 109 языков
  > Afrikaans
  > Alemannisch
  > Aragonés
  > العربية
  > مصرى
  > অসমীয়া
  > Asturianu
  > Azərbaycanca
  > تۆرکجه
  > Башҡортса
  > Беларуская
  > Betawi
  > Български
  > বাংলা
  > Brezhoneg
  > Bosanski
  > Català
  > کوردی
  > Čeština
  > Cymraeg
  > Dansk
  > Deutsch
  > Ελλ […] (24205 chars more)

## claim `ab704f02-9ed7-4351-8aeb-a576a08eb859`

- type: `external_fact`
- statement: Первый участок Рублёво-Архангельской линии Московского метрополитена открыт с пятью станциями.
- epistemic_status: `hypothesis` (head: current)
- grade: `E1`
- assessed_scope: `{"as_of": null, "scope_schema": "host-scope-v1", "source_domains": ["mos.ru", "kp.ru"]}`
- freshness: `fresh`

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T14:59:17.858030+00:00", "scope_schema": "host-scope-v1", "source_domain": "kp.ru"}`
- source: https://www.msk.kp.ru/daily/277813.5/5299000/ (content sha256 `e8c2f6e8a1420813…`) chunk `chunk-0`
- fragment:
  > Рублёво-Архангельская линия метро открыта в Москве: На карте, схема и фото станций графитовой ветки в 2026 году - KP.RU
  > Меню
  > Москва
  > Фото
  > Видео
  > Спецоперация
  > Политика
  > Общество
  > Экономика
  > В мире
  > Звезды
  > Здоровье
  > Соцподдержка
  > Наука
  > Спорт
  > Колумнисты
  > Происшествия
  > Национальные проекты России
  > Выбор экспертов
  > Афиша
  > Доктор
  > Финансы
  > Открываем мир
  > Я знаю
  > Семья
  > Женские секреты
  > Путеводитель
  > Книжная полка
  > Прогнозы на спорт
  > Промокоды
  > Сериалы
  > Спецпроекты
  > Кофе «Вкус Африки»
  > Дефицит железа
  > Туризм
  > Пресс-центр
  > Недвижимость
  > Телевизор
  > Коллекции
  > Правила применения рекомендательных технологий
  > Политика АО «ИД «Комсомольская правда» в отношении обработки персональных данных
  > Конкурсы
  > Работа у нас
  > Гид потребителя
  > Все о КП
  > Радио КП
  > Реклама
  > Тесты
  > Еще
  > Радио
  > Меню
  > Москва
  > Фото
  > Видео
  > Спецоперация
  > Политика
  > Общество
  > Экономика
  > В мире
  > Звезды
  > Здоровье
  > Соцподдержка
  > Наука
  > Спорт
  > Колумнисты
  > Происшествия
  > Национальные проекты России
  > Выбор экспертов
  > Афиша
  > Доктор
  > Финансы
  > Открываем мир
  > Я знаю
  > Семья
  > Женские секреты
  > Путеводитель
  > Книжная полка
  > Прогнозы на спорт
  > Промокоды
  > Сериалы
  > Спецпроекты
  > Кофе «Вкус Африки»
  > Дефицит железа
  > Туризм
  > Пресс-центр
  > Недвижимость
  > Телевизор
  > Коллекции
  > Правила применения рекомендательных технологий
  > Политика АО «ИД «Комсомольская правда» в отношении обработки персональных данных
  > Конкурсы
  > Работа у нас
  > Гид потребителя
  > Все о КП
  > Радио КП
  > Реклама
  > Тесты
  > Еще
  > Радио
  > Москва
  > +18
  > °
  > Радио
  > Реклама
  > Подписка
  > Сегодня:
  > Новости
  > Выборы 2026
  > Только у нас
  > Военкоры
  > Украина: сводка
  > КП в МАХ
  > Отдых в России
  > Заповедная Россия
  > Происшествия
  > Афиша
  > Испытано на себе
  > Еще
  > Общество
  > 5 сентября 2026 13:15
  > Рублево-Архангельская линия метро открыта в Москве:
  >  как выглядят станции и карта графитовой ветки
  > KP.RU публикует карту открытой Рублево-Архангельской линии метро
  > Алиса ТИТКО
  > Подписаться
  > Поделиться
  > Запуск первого участка Рублево-Архангельской линии - это 8,7 км путей и пять станций: «Деловой центр», «Шелепиха», «Звенигородская», «Народное Ополчение» и «Бульвар Генерала Карбышева»
  > Фото: 
  > Иван МАКЕЕВ. 
  > Перейти в Фотобанк КП
  > Открытие Ру […] (5268 chars more)

## claim `0ba8a00c-f6e8-45c3-bf60-c346a478c0f3`

- type: `external_fact`
- statement: Действующая Конституция Российской Федерации принята всенародным голосованием 12 декабря 1993 года.
- epistemic_status: `hypothesis` (head: current)
- grade: `E1`
- assessed_scope: `{"as_of": null, "scope_schema": "host-scope-v1", "source_domains": ["wikipedia.org", "gov.ru"]}`
- freshness: `fresh`

### evidence 1: `source_assertion` / `supports`
- scope: `{"as_of": "2026-09-19T16:12:49.098759+00:00", "scope_schema": "host-scope-v1", "source_domain": "gov.ru"}`
- source: http://duma.gov.ru/legislative/documents/constitution/ (content sha256 `f88f2d22bcf8377a…`) chunk `chunk-0`
- fragment:
  > Конституция РФ
  > 
  >             В социальных сетях
  >         
  > English 
  > Español 
  > 中文 
  > عربي 
  > 
  >                     Поиск
  >                 
  > Государственная Дума
  > Федерального Собрания Российской Федерации
  > 
  >                             ГД
  >                         
  > Новости
  > Структура
  > Фото и видео
  > Сервисы
  > Деятельность
  > Законодательная
  > Представительная
  > Международная
  > 
  >                                 Поиск
  >                             
  > Законотворчество
  > Планирование и документы
  > Рассмотрение
  > Результаты голосований
  > Стенограммы
  > СОЗД
  > Конституция РФ
  > Принята всенародным голосованием 12 декабря 1993 года с изменениями, одобренными в ходе общероссийского голосования 1 июля 2020 года. 
  > Мы, многонациональный народ Российской Федерации,
  > соединенные общей судьбой на своей земле,
  > утверждая права и свободы человека, гражданский мир и согласие,
  > сохраняя исторически сложившееся государственное единство, 
  > исходя из общепризнанных принципов равноправия и самоопределения народов,
  > чтя память предков, передавших нам любовь и уважение к Отечеству, веру в добро и справедливость,
  > возрождая суверенную государственность России и утверждая незыблемость ее демократической основы,
  > стремясь обеспечить благополучие и процветание России, 
  > исходя из ответственности за свою Родину перед нынешним и будущими поколениями,
  > сознавая себя частью мирового сообщества,
  > принимаем КОНСТИТУЦИЮ РОССИЙСКОЙ ФЕДЕРАЦИИ.
  > РАЗДЕЛ ПЕРВЫЙ
  > Глава 1. Основы конституционного строя
  > Статья 1
  > 1. Российская Федерация — Россия есть демократическое федеративное правовое государство с республиканской формой правления.
  > 2. Наименования Российская Федерация и Россия равнозначны.
  > Статья 2
  > Человек, его права и свободы являются высшей ценностью. Признание, соблюдение и защита прав и свобод человека и гражданина — обязанность государства.
  > Статья 3
  > 1. Носителем суверенитета и единственным источником власти в Российской Федерации является ее многонациональный народ.
  > 2. Народ осуществляет свою власть непосредственно, а также через органы государственной власти и орг […] (115004 chars more)
