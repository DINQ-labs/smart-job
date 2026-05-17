/**
 * TokenStore: 管理 Boss直聘 会话令牌和职位令牌链。
 * 内存存储 + chrome.storage 持久化。
 * 令牌链: listSecurityId → detailSecurityId → chatSecurityId
 *
 * Round-3 audit HIGH 5(2026-04-28):**多用户安全分区**
 * 此前 jobs / geeks / _chains 是 process-global 字典,同一 Chrome profile
 * 切换 DINQ 用户(或同一 DINQ 用户切多账号)时,A 写入的 token 会被 B 读到 →
 * Indeed RCP 跨用户污染、Boss 跨账号查 boss 详情拿到错的 securityId 等。
 *
 * 修法:per-user 分区 + 用户切换时 clear。activeUserId 由 background.js 在
 * PERSONAL_USER_ID 变化时通过 setActiveUser(uid) 同步,clearUserData(prevUid)
 * 顺手清旧 partition。
 *
 * 兼容:setActiveUser(uid='') 时 fallback 到 default partition,旧用户启动不
 * 用必须先调本方法,首次访问写 default。
 */

const TOKEN_STORAGE_KEY = 'boss_api_tokens';
const TOKEN_TTL_MS = 5 * 60 * 1000; // 5分钟
const _DEFAULT_USER_PARTITION = '__default__';

class TokenStore {
  constructor() {
    /** 当前活跃用户 ID(DINQ user_id);'' 时落 default partition。
     *  setActiveUser(uid) 切换;切换时不自动清 prev,由调用方决定。 */
    this._activeUserId = _DEFAULT_USER_PARTITION;
    /** per-user 分区: { [userId]: { jobs, geeks, _chains } } */
    this._partitions = { [_DEFAULT_USER_PARTITION]: { jobs: {}, geeks: {}, _chains: {} } };
    this.session = {
      wt2: '',
      userId: '',
      encryptUserId: '',
      name: '',
      /** true = 招聘方(boss/HR), false = 求职者(geek), undefined = 未知 */
      identity: undefined,
      /** 是否同时拥有求职+招聘双重身份 */
      doubleIdentity: false,
      deviceId: '',
      guid: '',
      zpStoken: '',            // __zp_stoken__ cookie，zp_token 轮转链的真实来源
      zpStokenUpdatedAt: 0,   // 最后一次轮转时间戳（ms），供 gateway 判断新鲜度
      bossToken: '',           // Boss 请求的短 token 头（来自 Boss 自己 JS 的 token header）
    };
  }

  /** 取当前 active user 的分区(必须存在 — 默认有 _DEFAULT_USER_PARTITION) */
  _part() {
    let p = this._partitions[this._activeUserId];
    if (!p) {
      p = this._partitions[this._activeUserId] = { jobs: {}, geeks: {}, _chains: {} };
    }
    return p;
  }

  // 兼容老接口:this.jobs / this.geeks / this._chains 通过 getter 路由到当前
  // active 分区。已有大量代码直接读这三个属性,加 getter 避免大规模 refactor。
  get jobs()    { return this._part().jobs; }
  set jobs(v)   { this._part().jobs = v; }
  get geeks()   { return this._part().geeks; }
  set geeks(v)  { this._part().geeks = v; }
  get _chains() { return this._part()._chains; }
  set _chains(v){ this._part()._chains = v; }

  /** 切换 active user;切换后所有 jobs/geeks/_chains 访问自动落到对应分区。
   *  触发时机:background.js 在 PERSONAL_USER_ID 变化时调用。
   *  返回 prev userId 给调用方做 clearUserData(prev) 决定。 */
  setActiveUser(userId) {
    const prev = this._activeUserId;
    this._activeUserId = userId || _DEFAULT_USER_PARTITION;
    if (!this._partitions[this._activeUserId]) {
      this._partitions[this._activeUserId] = { jobs: {}, geeks: {}, _chains: {} };
    }
    return prev;
  }

  getActiveUser() {
    return this._activeUserId === _DEFAULT_USER_PARTITION ? '' : this._activeUserId;
  }

  /** 清空指定用户的所有 token chain 数据(账号切换 / 登出时调)。
   *  不传 userId 时清空 default partition。 */
  async clearUserData(userId) {
    const key = userId || _DEFAULT_USER_PARTITION;
    delete this._partitions[key];
    if (key === this._activeUserId) {
      // 当前 active 被清,重建空 partition
      this._partitions[key] = { jobs: {}, geeks: {}, _chains: {} };
    }
    await this._save();
  }

  /** 从 chrome.storage 恢复持久化状态。
   *  v2 schema(per-user partition):{ session, partitions: { [uid]: {jobs,geeks,chains} }, activeUserId }
   *  v1 schema(老 flat):{ session, jobs, geeks, chains } —— 加载后落到 default partition,
   *  确保升级路径无数据丢失。 */
  async load() {
    try {
      const data = await chrome.storage.local.get(TOKEN_STORAGE_KEY);
      const stored = data[TOKEN_STORAGE_KEY];
      if (stored && typeof stored === 'object') {
        if (stored.session) Object.assign(this.session, stored.session);
        if (stored.partitions && typeof stored.partitions === 'object') {
          // v2 schema
          this._partitions = stored.partitions;
          if (!this._partitions[_DEFAULT_USER_PARTITION]) {
            this._partitions[_DEFAULT_USER_PARTITION] = { jobs: {}, geeks: {}, _chains: {} };
          }
          this._activeUserId = stored.activeUserId || _DEFAULT_USER_PARTITION;
        } else if (stored.jobs || stored.geeks || stored.chains) {
          // v1 schema 自动迁移到 default partition(老用户升级首次启动会走这里)
          this._partitions[_DEFAULT_USER_PARTITION] = {
            jobs: stored.jobs || {},
            geeks: stored.geeks || {},
            _chains: stored.chains || {},
          };
          console.log('[TokenStore] migrated v1 → v2 schema (default partition)');
        }
      }
    } catch (e) {
      console.warn('[TokenStore] load error:', e);
    }
  }

  /** 持久化到 chrome.storage(v2 schema) */
  async _save() {
    try {
      // 深 clone 避免 chrome.storage 反序列化引用问题
      const partitions = {};
      for (const [uid, part] of Object.entries(this._partitions)) {
        partitions[uid] = {
          jobs: { ...part.jobs },
          geeks: { ...part.geeks },
          _chains: { ...part._chains },
        };
      }
      await chrome.storage.local.set({
        [TOKEN_STORAGE_KEY]: {
          session: { ...this.session },
          partitions,
          activeUserId: this._activeUserId,
        },
      });
    } catch (e) {
      console.warn('[TokenStore] save error:', e);
    }
  }

  /** 更新 session 信息（设备指纹、用户信息） */
  updateSession(data) {
    Object.assign(this.session, data);
    this._save();
  }

  /** 存储职位令牌（来自搜索结果列表，批量） */
  async storeJobListTokens(jobs) {
    const now = Date.now();
    for (const job of jobs) {
      const id = job.encryptJobId || job.jobId;
      if (!id) continue;
      const existing = this.jobs[id] || {};
      this.jobs[id] = {
        ...existing,
        listSecurityId: job.securityId || existing.listSecurityId || '',
        bossId: job.encryptBossId || job.bossId || existing.bossId || '',
        encryptBossId: job.encryptBossId || existing.encryptBossId || '',
        lid: job.lid || existing.lid || '',
        timestamp: now,
      };
    }
    await this._save();
  }

  /** 存储职位详情令牌 */
  async storeDetailToken(encryptJobId, securityId, bossId) {
    const existing = this.jobs[encryptJobId] || {};
    this.jobs[encryptJobId] = {
      ...existing,
      detailSecurityId: securityId || '',
      bossId: bossId || existing.bossId || '',
      encryptBossId: bossId || existing.encryptBossId || '',
      timestamp: Date.now(),
    };
    await this._save();
  }

  /** 存储聊天令牌（friend/add 返回）
   *
   * 2026-04-28:接受 encryptBossId 参数。原签名只 (encryptJobId, securityId),
   * 当用户直接从 Boss 消息列表打开和某 boss 的对话(此前从未搜索过该 job)时,
   * tokenStore.jobs[encryptJobId] 是新建 entry,encryptBossId 为空 → 后端
   * lookupChatTokenByBoss 永远查不到。修法:friend/add intercept 时一并写入
   * encryptBossId,与 storeDetailToken 的字段保持一致。
   */
  async storeChatToken(encryptJobId, securityId, encryptBossId = '') {
    const existing = this.jobs[encryptJobId] || {};
    this.jobs[encryptJobId] = {
      ...existing,
      chatSecurityId: securityId || '',
      // 仅在已有字段为空时填充,避免覆盖此前 storeJobListTokens / storeDetailToken
      // 写入的更权威值(那些来自搜索 / 详情接口,可能更准确)。
      bossId: existing.bossId || encryptBossId || '',
      encryptBossId: existing.encryptBossId || encryptBossId || '',
      timestamp: Date.now(),
    };
    await this._save();
  }

  /** 获取职位所有令牌 */
  getJobTokens(encryptJobId) {
    return this.jobs[encryptJobId] || null;
  }

  /**
   * 标记某 job 的会话已 enterSession 激活(Boss 要求 sendMsg 前必须 enterSession,
   * Boss page 打开会话时会自动调,我们补齐)。已激活后无需重复 enter,sendMsg 直接成功。
   *
   * 2026-04-28 抓包反推:capture #2 显示每个 friend 都有一次 geekEnter POST,
   * body.securityId 与 stage 2 (getGeekFriendList) 返回的 securityId 完全一致。
   * 所以 enterSession 用的是同一个 chatSecurityId。
   */
  async markSessionEntered(encryptJobId) {
    if (!encryptJobId) return;
    const existing = this.jobs[encryptJobId] || {};
    this.jobs[encryptJobId] = {
      ...existing,
      sessionEntered: true,
      timestamp: Date.now(),
    };
    await this._save();
  }

  /**
   * 按 boss 反查 chat token —— "查看和某 boss 的聊天历史"路径专用。
   *
   * 用户在 Boss 浏览器里打开和某 boss 的对话页时,Boss 前端调 friend/add 接口,
   * 扩展 intercept 后调 storeChatToken(encryptJobId, securityId);但 (encryptJobId,
   * encryptBossId) 双键关系是在 storeDetailToken / storeJobListTokens 时建立的
   * (写入 this.jobs[encryptJobId].encryptBossId)。
   *
   * 反查策略:遍历 this.jobs,找到 encryptBossId 匹配 + chatSecurityId 非空的那行
   * 取最新一条(timestamp 最大)。
   *
   * @param {string} encryptBossId  对应 friendList 项里的 encryptFriendId
   * @returns {{ encryptJobId, chatSecurityId, capturedAt } | null}
   */
  lookupChatTokenByBoss(encryptBossId) {
    if (!encryptBossId) return null;
    let best = null;
    for (const [encryptJobId, entry] of Object.entries(this.jobs)) {
      if (!entry || !entry.chatSecurityId) continue;
      if (entry.encryptBossId !== encryptBossId && entry.bossId !== encryptBossId) continue;
      if (!best || (entry.timestamp || 0) > (best.timestamp || 0)) {
        best = { encryptJobId, ...entry };
      }
    }
    if (!best) return null;
    return {
      encryptJobId: best.encryptJobId,
      chatSecurityId: best.chatSecurityId,
      capturedAt: best.timestamp || 0,
    };
  }

  // ── 通用多命名空间 API（供 core/token-chain.js, core/request-builder.js 使用）──
  // Phase 3 shim：将新 API 映射到现有 jobs/geeks 结构，保持向后兼容。
  // Phase 3 完整实现时此处替换为真正的 this._ns 结构。

  /**
   * 获取指定链阶段的 token entry
   * @returns {{ value: string, ts: number, meta: object } | null}
   */
  getChainToken(namespace, entityKey, field) {
    let store;
    if (namespace === 'jobs')        store = this.jobs;
    else if (namespace === 'geeks')  store = this.geeks;
    else                             store = (this._chains[namespace] || {});

    const entry = store[entityKey];
    if (!entry || entry[field] == null) return null;
    return {
      value: entry[field],
      ts: entry.timestamp || 0,
      meta: this._extractChainMeta(namespace, entry),
    };
  }

  /**
   * 存储指定链阶段的 token（async — await _save() 防止 SW 重启时数据丢失）
   */
  async setChainToken(namespace, entityKey, field, value, meta = {}) {
    let store;
    if (namespace === 'jobs') {
      store = this.jobs;
    } else if (namespace === 'geeks') {
      store = this.geeks;
    } else {
      if (!this._chains[namespace]) this._chains[namespace] = {};
      store = this._chains[namespace];
    }
    if (!store[entityKey]) store[entityKey] = {};
    store[entityKey][field] = value;
    store[entityKey].timestamp = Date.now();
    if (meta) Object.assign(store[entityKey], meta);
    await this._save();
  }

  /**
   * 批量存储同一命名空间下多个 token，最后只调用一次 _save()（性能优化）
   * @param {string} namespace
   * @param {Array<{ entityKey: string, field: string, value: any, meta?: object }>} entries
   */
  async _batchSetChainTokens(namespace, entries) {
    let store;
    if (namespace === 'jobs') {
      store = this.jobs;
    } else if (namespace === 'geeks') {
      store = this.geeks;
    } else {
      if (!this._chains[namespace]) this._chains[namespace] = {};
      store = this._chains[namespace];
    }
    const now = Date.now();
    for (const { entityKey, field, value, meta = {} } of entries) {
      if (!store[entityKey]) store[entityKey] = {};
      store[entityKey][field] = value;
      store[entityKey].timestamp = now;
      Object.assign(store[entityKey], meta);
    }
    await this._save();
  }

  /** 提取 meta 附属字段（供 getChainToken 使用） */
  _extractChainMeta(namespace, entry) {
    if (namespace === 'jobs') {
      return {
        encryptBossId: entry.encryptBossId || entry.bossId || '',
        lid:           entry.lid || '',
      };
    }
    if (namespace === 'geeks') {
      return {
        encryptExpectId: entry.encryptExpectId || '',
        encryptJobId:    entry.encryptJobId || '',
      };
    }
    if (namespace === 'indeed_jobs' || namespace === 'linkedin_jobs') {
      return {
        trackingKey: entry.trackingKey || '',
        title:       entry.title || '',
        company:     entry.company || '',
        location:    entry.location || '',
      };
    }
    return {};
  }

  storeGeekToken(encryptUid, data) {
    const existing = this.geeks[encryptUid] || {};
    this.geeks[encryptUid] = { ...existing, ...data, timestamp: Date.now() };
    this._save();
  }

  getGeekToken(encryptUid) { return this.geeks[encryptUid] || null; }

  isGeekStale(encryptUid) {
    const t = this.geeks[encryptUid];
    if (!t) return true;
    return Date.now() - (t.timestamp || 0) > TOKEN_TTL_MS;
  }

  /** 检查令牌是否过期（超过 TTL） */
  isStale(encryptJobId) {
    const t = this.jobs[encryptJobId];
    if (!t) return true;
    return Date.now() - (t.timestamp || 0) > TOKEN_TTL_MS;
  }

  /** 返回 session + jobs + geeks + chains 快照 */
  snapshot() {
    return {
      session: { ...this.session },
      jobs: JSON.parse(JSON.stringify(this.jobs)),
      geeks: JSON.parse(JSON.stringify(this.geeks)),
      chains: JSON.parse(JSON.stringify(this._chains)),
    };
  }
}

// 单例导出（Service Worker 全局使用）
const tokenStore = new TokenStore();
