/* Owner-only navigation for the read-only company analytics workspace. Tabs show persisted findings only after they are connected. */
(()=>{'use strict';
  const root=document.getElementById('aiAnalyticsPage'),host=document.getElementById('aiBusinessAnalystHost');
  if(!root||!host)return;
  const copy={
    ru:{title:'AI-аналитика',intro:'Риски клиентов, проблемные диалоги и задачи — с проверяемыми источниками.',tabs:[['overview','Обзор рисков'],['customers','Недовольные клиенты'],['dialogs','Проблемные диалоги'],['tasks','Просроченные и блокирующие задачи'],['patterns','Повторяющиеся проблемы'],['actions','Рекомендации по действиям'],['history','История изменений'],['settings','Настройки']],readyTitle:'Раздел готов к подключению результатов анализа',readyText:'Здесь будут отображаться найденные сигналы, доказательства и ссылки на исходные записи. Пока этот список не подключён к карточкам анализа; используйте вопрос AI-аналитику в обзоре. Источники, права доступа и полнота должны быть проверены перед выводом данных.',settingsTitle:'Параметры аналитики',settingsText:'Подключения источников, пороги риска и периодичность анализа будут настраиваться здесь после серверного подключения. Сейчас настройки не меняют реальные источники или частоту запуска.',readonly:'Только чтение · сообщения клиентам не отправляются'},
    az:{title:'AI analitikası',intro:'Müştəri riskləri, problemli dialoqlar və tapşırıqlar — yoxlanıla bilən mənbələrlə.',tabs:[['overview','Risklərə ümumi baxış'],['customers','Narazı müştərilər'],['dialogs','Problemli dialoqlar'],['tasks','Gecikmiş və bloklayan tapşırıqlar'],['patterns','Təkrarlanan problemlər'],['actions','Fəaliyyət tövsiyələri'],['history','Dəyişiklik tarixçəsi'],['settings','Ayarlar']],readyTitle:'Təhlil nəticələri bu bölməyə qoşulacaq',readyText:'Burada aşkar edilmiş siqnallar, sübutlar və ilkin qeydlərə keçidlər görünəcək. Hazırda bu siyahı təhlil kartlarına qoşulmayıb; ümumi baxışda AI analitikə sual verə bilərsiniz. Məlumat göstərilməzdən əvvəl mənbələr, giriş hüquqları və əhatə yoxlanmalıdır.',settingsTitle:'Təhlil parametrləri',settingsText:'Mənbə bağlantıları, risk hədləri və təhlil tezliyi server tərəfdə qoşulduqdan sonra burada idarə olunacaq. Hazırda ayarlar real mənbələrə və işə salınma tezliyinə təsir etmir.',readonly:'Yalnız oxu · müştərilərə mesaj göndərilmir'},
    en:{title:'AI Analytics',intro:'Customer risks, problematic conversations, and tasks with traceable sources.',tabs:[['overview','Risk overview'],['customers','Unhappy customers'],['dialogs','Problem conversations'],['tasks','Overdue and blocking tasks'],['patterns','Recurring problems'],['actions','Recommended actions'],['history','Change history'],['settings','Settings']],readyTitle:'Analysis results will appear here when connected',readyText:'This section will show detected signals, evidence, and links to source records. Its list is not yet connected to persisted analysis findings; use the AI analyst in Overview for now. Sources, access rights, and coverage must be checked before displaying records.',settingsTitle:'Analytics settings',settingsText:'Source connections, risk thresholds, and analysis frequency will be managed here after the server integration is completed. These settings currently do not change live sources or run frequency.',readonly:'Read-only · no customer messages are sent'}
  };
  const lang=()=>copy[window.tenantI18n?.language]||copy.ru;
  function render(){
    const w=lang();root.replaceChildren();const title=document.querySelector('[data-ai-analytics-title]'),intro=document.querySelector('[data-ai-analytics-intro]');if(title)title.textContent=w.title;if(intro)intro.textContent=w.intro;
    const nav=document.createElement('div');nav.className='app-page-tabs';nav.setAttribute('role','tablist');nav.setAttribute('aria-label',w.tabs[0][1]);
    const panels=new Map();
    for(const [id,label] of w.tabs){
      const tab=document.createElement('button');tab.type='button';tab.id=`ai-analytics-tab-${id}`;tab.dataset.analyticsTab=id;tab.setAttribute('role','tab');tab.setAttribute('aria-controls',`ai-analytics-panel-${id}`);tab.textContent=label;
      const panel=document.createElement('section');panel.id=`ai-analytics-panel-${id}`;panel.dataset.analyticsPanel=id;panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby',tab.id);panel.hidden=true;
      nav.append(tab);root.append(panel);panels.set(id,panel);
      tab.addEventListener('click',()=>activate(id));
    }
    const overview=panels.get('overview');
    const note=document.createElement('p');note.className='sub';note.textContent=w.readonly;overview.append(note,host);
    for(const [id] of w.tabs.slice(1)){
      const panel=panels.get(id),card=document.createElement('article');card.className='panel';
      const title=document.createElement('h2');title.textContent=id==='settings'?w.settingsTitle:w.readyTitle;
      const text=document.createElement('p');text.textContent=id==='settings'?w.settingsText:w.readyText;
      card.append(title,text);panel.append(card);
    }
    root.prepend(nav);activate('overview');
    function activate(id){
      nav.querySelectorAll('[role="tab"]').forEach(tab=>{const active=tab.dataset.analyticsTab===id;tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;});
      panels.forEach((panel,key)=>panel.hidden=key!==id);
    }
  }
  render();document.addEventListener('tenant-language',render);
})();
