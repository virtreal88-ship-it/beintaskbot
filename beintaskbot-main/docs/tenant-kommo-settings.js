/* Owner-only manual Kommo connection. Long-lived tokens are never retained in the page. */
(() => {
  const $ = id => document.getElementById(id);
  let profile, generation = 0, busy = false;

  async function api(url, data) {
    const response = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      ...(data ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) } : {}),
    });
    const result = await response.json();
    if (!response.ok || !result.success) throw new Error(result.error || "Kommo ayarları yüklənmədi.");
    return result;
  }

  function disable(on) {
    busy = on;
    $("tenantKommoPanel")?.querySelectorAll("button,input").forEach(element => { element.disabled = on; });
  }

  async function perform(work) {
    if (busy) return;
    const current = generation;
    disable(true);
    $("tkMessage").textContent = "Yüklənir…";
    try {
      await work(current);
      if (current === generation) $("tkMessage").textContent = "Hazırdır.";
    } catch (error) {
      if (current === generation) $("tkMessage").textContent = error.message;
    } finally {
      if (current === generation) disable(false);
    }
  }

  async function load(current) {
    $("tkBody").hidden = false;
    $("tkToken").value = "";
    $("tkConfirm").checked = false;
    try {
      const result = await api("/api/platform/integrations/kommo/test");
      if (current !== generation) return;
      if (result.auth_mode === "oauth") {
        $("tkStatus").textContent = "Kommo-nun rəsmi bağlantısı aktivdir · " + (result.account?.name || "Kommo hesabı");
        $("tkManual").hidden = true;
        $("tkDisconnect").hidden = true;
      } else {
        $("tkStatus").textContent = "Əl ilə Kommo bağlantısı işləyir · " + (result.account?.name || "Kommo hesabı");
        $("tkManual").hidden = false;
        $("tkDisconnect").hidden = false;
      }
    } catch (error) {
      if (current !== generation) return;
      $("tkStatus").textContent = error.message || "Kommo hələ qoşulmayıb.";
      $("tkManual").hidden = false;
      $("tkDisconnect").hidden = true;
    }
  }

  function install(data) {
    const next = data.member;
    const identity = next.tenant_id + ":" + next.telegram_id + ":" + next.role;
    if (profile?.identity === identity) return;
    generation++;
    profile = { ...next, identity };
    window.tenantViewDemand.clean("#tenantKommoPanel");
    $("tenantKommoPanel")?.remove();
    if (next.role !== "owner") return;
    const settings = $("view-settings");
    if (!settings) return;

    const panel = document.createElement("article");
    panel.className = "panel";
    panel.id = "tenantKommoPanel";
    panel.style.marginTop = "16px";
    panel.innerHTML = [
      "<h2>Kommo · əl ilə bağlantı</h2>",
      "<p>Rəsmi OAuth bağlantısı hazır olana qədər şirkət hesabını long-lived token ilə qoşa bilərsiniz.</p>",
      '<button class="outline" id="tkLoad" type="button">Bağlantını yoxla</button>',
      '<div id="tkBody" hidden>',
      '<p id="tkStatus" role="status"></p>',
      '<div id="tkManual">',
      '<p>Mövcud şəxsi Kommo inteqrasiyanızın long-lived tokenindən istifadə edin; yenisini yaratmağa ehtiyac yoxdur. Token güclü giriş hüquqlarına malik ola bilər və Kommo-da ləğv edilə bilər. Token yalnız Kommo hesabında yoxlanılır, serverdə şifrəli saxlanır və yenidən göstərilmir. Tokeni söhbətdə göndərməyin.</p>',
      '<p><a href="https://developers.kommo.com/docs/long-lived-token" target="_blank" rel="noopener noreferrer">Kommo long-lived token təlimatı</a></p>',
      '<label>Kommo hesab ünvanı<input id="tkDomain" type="text" autocomplete="url" placeholder="şirkətiniz.kommo.com"></label>',
      '<label>Long-lived token<input id="tkToken" type="password" autocomplete="new-password" placeholder="Yeni token (saxlanmış token göstərilmir)"></label>',
      '<label style="display:flex;align-items:flex-start"><input id="tkConfirm" type="checkbox" style="width:auto;margin-right:8px">Tokenin Kommo icazələrini bu şirkət adından istifadə etməsinə razıyam.</label>',
      '<button class="outline" id="tkConnect" type="button">Yoxla və qoş</button>',
      "</div>",
      '<button class="outline danger" id="tkDisconnect" type="button" hidden>Əl ilə bağlantını ayır</button>',
      "</div>",
      '<p id="tkMessage" role="status" aria-live="polite"></p>',
    ].join("");
    settings.appendChild(panel);

    $("tkLoad").onclick = () => perform(load);
    $("tkConnect").onclick = () => perform(async current => {
      const token = $("tkToken").value;
      const accountDomain = $("tkDomain").value.trim();
      if (!token) throw new Error("Kommo long-lived tokenini daxil edin.");
      if (!$("tkConfirm").checked) throw new Error("Token icazələrini təsdiqləyin.");
      $("tkToken").value = "";
      await api("/api/platform/integrations/kommo", {
        action: "manual_connect",
        account_domain: accountDomain,
        access_token: token,
        expected_tenant_id: profile.tenant_id,
        expected_user_id: profile.telegram_id,
        confirm_long_lived_token: true,
      });
      if (current === generation) await load(current);
    });
    $("tkDisconnect").onclick = () => {
      if (!window.confirm("Kommo bağlantısını bu şirkətdən ayırmaq istəyirsiniz?")) return;
      perform(async current => {
        await api("/api/platform/integrations/disconnect", { provider: "kommo" });
        if (current === generation) await load(current);
      });
    };
    void load(generation);
  }

  window.tenantViewDemand.watch({
    provider: "kommo-manual",
    editable: "#tenantKommoPanel",
    activate: install,
    reset: () => {
      generation++;
      profile = null;
      busy = false;
      window.tenantViewDemand.clean("#tenantKommoPanel");
      $("tenantKommoPanel")?.remove();
    },
  });
})();
