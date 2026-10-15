(() => {
  const languages = ['en', 'az', 'ru'];
  const labels = { en: 'English', az: 'Azərbaycan', ru: 'Русский' };
  const text = {
    en: {
      '.nav-links:not(.language-switch) a':['Features','How it works','News','Getting started'], '.support-link':['Support'], '.language-menu summary':['English ▾'],
      '.login[data-copy="connect"]':['Beintaskbot Connect →'], '.login[data-copy="login"]':['Sign in →'],
      '.eyebrow':['One workspace for your business'], 'h1':['Manage sales, customers, and your team in one place.'], '.lead':['CRM Smart Assistant brings your daily work together: customer requests, deals, tasks, and team activity on one screen.'],
      '.hero-actions .primary':['Start for free →'], '.hero-actions .text-link':['How to get started →'], '.dash-top span:first-child':['CRM Smart Assistant · Today at a glance'], '.metric:nth-child(1) small':['New requests'], '.metric:nth-child(2) small':['Active tasks'],
      '.deal-card:nth-child(1) small':['Deal'], '.deal-card:nth-child(1) b':['Customer follow-up'], '.deal-card:nth-child(1) .tag':['In progress'], '.deal-card:nth-child(2) small':['Task'], '.deal-card:nth-child(2) b':['Today’s top priority'], '.deal-card:nth-child(2) .tag':['Today'],
      '#imkanlar .section-title span':['One platform'], '#imkanlar h2':['Every stage of your workflow, in view'], '#imkanlar .section-title p':['Your team’s essential tools work together, so information stays in one system.'],
      '#imkanlar .feature h3':['Deals','Tasks','Unified conversation history','AI replies and summaries','Kommo integration','Linear integration','Priority orders and balances','Roles and permissions','Smart notifications'],
      '#imkanlar .feature p':['Track every stage from a new request to a sale, with a clear owner and next step.','Create tasks, assign an owner and due date, and keep priorities and overdue work visible.','Bring WhatsApp, Instagram, and other connected conversations into one customer record, including photos, video, and voice messages.','AI drafts replies for a manager to review and summarizes conversations and voice messages. Turn a summary into a task or note in one click.','Sync Kommo customers, leads, stages, tasks, and notes with your workflow — without making staff learn another CRM.','Give directors a focused approval list for selected Linear tasks. Approve, send back for discussion, or open them in Linear.','Post an order for the team: the first available colleague can accept it. Confirm the amount after the work and track the pending balance.','Each user sees only the sections and information they need. Administrators can manage the whole workflow.','Send new requests, tasks, and important updates to the right colleague so no lead is missed.'],
      '.workflow .section-kicker':['A simple process'], '.workflow h2':['A clear path from request to result'], '.workflow > div:first-child p':['See who is doing what, what comes next, and make sure every request gets followed up.'], '.step b':['A request enters the system','Work goes to the right person','Results and oversight'], '.step span':['A new customer or order is recorded and routed to the right stage.','The owner sees the task and all relevant customer context together.','Progress, stages, notes, and payment details stay in the system.'],
      '#istifadeci .section-title span':['For every role'], '#istifadeci h2':['Give every teammate a workspace that fits'], '#istifadeci .section-title p':['The interface adapts to each person’s role, without clutter or unnecessary information.'], '.role b':['Administrator','Sales and office team','Field team'], '.role p':['Manages colleagues, permissions, workflows, orders, and overall results.','Works with their customers, tasks, conversations, and daily priorities.','Accepts available orders and tracks progress and their own work balance.'],
      '.cta h2':['Run your business with clarity.'], '.cta > p:first-of-type':['CRM Smart Assistant helps sales, service, and operations teams organize their daily work.'], '.cta .primary':['Set up your company →'], '.cta .text-link':['Discover Beintaskbot Connect for WhatsApp channels →'], '.footer-line span:first-child':['© 2026 BeinSystems'], '.footer-line span:last-child':['CRM Smart Assistant · Business and customer management']
    },
    ru: {
      '.nav-links:not(.language-switch) a':['Возможности','Как это работает','Новости','Начало работы'], '.support-link':['Поддержка'], '.language-menu summary':['Русский ▾'],
      '.login[data-copy="connect"]':['Beintaskbot Connect →'], '.login[data-copy="login"]':['Войти →'],
      '.eyebrow':['Единая рабочая система для бизнеса'], 'h1':['Управляйте продажами, клиентами и командой в одном месте.'], '.lead':['CRM Smart Assistant — платформа BeinSystems, которая упрощает ежедневную работу: обращения клиентов, сделки, задачи и работа команды на одном экране.'],
      '.hero-actions .primary':['Начать бесплатно →'], '.hero-actions .text-link':['Как начать →'], '.dash-top span:first-child':['CRM Smart Assistant · Обзор на сегодня'], '.metric:nth-child(1) small':['Новые обращения'], '.metric:nth-child(2) small':['Активные задачи'],
      '.deal-card:nth-child(1) small':['Сделка'], '.deal-card:nth-child(1) b':['Связаться с клиентом'], '.deal-card:nth-child(1) .tag':['В работе'], '.deal-card:nth-child(2) small':['Задача'], '.deal-card:nth-child(2) b':['Главный приоритет дня'], '.deal-card:nth-child(2) .tag':['Сегодня'],
      '#imkanlar .section-title span':['Одна платформа'], '#imkanlar h2':['Все этапы рабочего процесса под контролем'], '#imkanlar .section-title p':['Основные инструменты команды работают вместе, а данные остаются в одной системе.'],
      '#imkanlar .feature h3':['Сделки','Задачи','Единая история общения','Ответы и сводки ИИ','Интеграция с Kommo','Интеграция с Linear','Срочные заказы и баланс','Роли и права доступа','Умные уведомления'],
      '#imkanlar .feature p':['Отслеживайте путь от обращения до продажи, ответственного и следующего шага.','Создавайте задачи, назначайте исполнителя и срок. Приоритеты и просрочки всегда на виду.','Собирайте переписки из WhatsApp, Instagram и других подключённых источников в карточке клиента, включая фото, видео и голосовые сообщения.','ИИ подготовит черновик ответа для менеджера и кратко изложит переписку и голосовые сообщения. Создайте задачу или заметку по сводке в один клик.','Клиенты, лиды, этапы, задачи и заметки из Kommo синхронизируются с вашим процессом — сотрудникам не нужно осваивать ещё одну CRM.','Руководитель видит отдельный список выбранных задач Linear на подтверждение. Подтвердите, верните на обсуждение или откройте задачу в Linear.','Опубликуйте заказ для команды: его примет первый свободный сотрудник. Подтвердите сумму после выполнения и отслеживайте ожидающий баланс.','Каждый пользователь видит только нужные ему разделы и данные. Администратор управляет всем процессом.','Новые обращения, задачи и важные изменения приходят нужным сотрудникам — ни один лид не потеряется.'],
      '.workflow .section-kicker':['Простой процесс'], '.workflow h2':['Понятный путь от обращения до результата'], '.workflow > div:first-child p':['CRM Smart Assistant показывает, кто и что делает, какой шаг следующий, и помогает не забыть ни одно обращение.'], '.step b':['Обращение попадает в систему','Задача передаётся нужному сотруднику','Результат и контроль'], '.step span':['Новый клиент или заказ фиксируется и направляется на нужный этап.','Ответственный видит задачу и весь контекст клиента в одном месте.','В системе сохраняются выполнение, этапы, заметки и платёжные данные.'],
      '#istifadeci .section-title span':['Для каждой роли'], '#istifadeci h2':['Каждому сотруднику — своё рабочее пространство'], '#istifadeci .section-title p':['Интерфейс учитывает роль пользователя — без сложных меню и лишних данных.'], '.role b':['Администратор','Продажи и офис','Исполнитель'], '.role p':['Управляет сотрудниками, правами, процессами, заказами и общими результатами.','Работает со своими клиентами, задачами, переписками и ежедневными приоритетами.','Принимает доступные заказы, отслеживает выполнение и свой рабочий баланс.'],
      '.cta h2':['Управляйте бизнесом понятнее.'], '.cta > p:first-of-type':['CRM Smart Assistant помогает командам продаж, сервиса и операций организовать ежедневную работу.'], '.cta .primary':['Настроить компанию →'], '.cta .text-link':['Узнайте о Beintaskbot Connect для каналов WhatsApp →'], '.footer-line span:first-child':['© 2026 BeinSystems'], '.footer-line span:last-child':['CRM Smart Assistant · Управление работой и клиентами']
    }
  };
  const selectors = Object.keys(text.en);
  const originals = new Map(selectors.map(selector => [selector, Array.from(document.querySelectorAll(selector), node => node.querySelector('span') ? node.firstChild.nodeValue : node.textContent)]));
  function apply(language, save = true) {
    if (!languages.includes(language)) language = 'en';
    document.documentElement.lang = language;
    document.querySelectorAll('[data-site-language]').forEach(link => {
      link.setAttribute('aria-current', String(link.dataset.siteLanguage === language));
      link.href = `/?site_lang=${link.dataset.siteLanguage}`;
    });
    const dict = language === 'az' ? null : text[language];
    selectors.forEach(selector => {
      document.querySelectorAll(selector).forEach((node, i) => {
        const value = dict ? dict[selector]?.[i] : originals.get(selector)?.[i];
        if (value === undefined) return;
        if (node.querySelector('span')) {
          const textNode = Array.from(node.childNodes).find(child => child.nodeType === Node.TEXT_NODE && child.nodeValue.trim());
          if (textNode) textNode.nodeValue = ` ${value} `;
        }
        else node.textContent = value;
      });
    });
    const summary = document.querySelector('.language-menu summary');
    summary.textContent = `${labels[language]} ▾`;
    document.querySelector('.login[data-copy="connect"]').href = `/connector/?lang=${language}`;
    document.querySelector('.cta .text-link').href = `/connector/?lang=${language}`;
    document.querySelector('meta[name="description"]').content = ({en:'CRM Smart Assistant brings customer management, tasks, sales, and team workflows together.',az:'CRM Smart Assistant — satış, müştəri, tapşırıq və komandanı idarə etmək üçün BeinSystems platforması.',ru:'CRM Smart Assistant объединяет управление клиентами, продажами, задачами и командой.'})[language];
    if (save) {
      localStorage.setItem('crm.site.language', language);
      document.cookie = `site_language=${language}; Max-Age=31536000; Path=/; SameSite=Lax; Secure`;
    }
  }
  const allowedUrlLanguage = new URLSearchParams(location.search).get('site_lang');
  let initial = allowedUrlLanguage || localStorage.getItem('crm.site.language') || 'en';
  if (!languages.includes(initial)) initial = 'en';
  apply(initial, Boolean(allowedUrlLanguage));
  document.querySelectorAll('[data-site-language]').forEach(link => link.addEventListener('click', event => {
    event.preventDefault(); apply(link.dataset.siteLanguage); document.querySelector('.language-menu').open = false;
    history.replaceState(null, '', `/?site_lang=${link.dataset.siteLanguage}`);
  }));
  document.addEventListener('click', event => { if (!event.target.closest('.language-menu')) document.querySelector('.language-menu').open = false; });
})();
