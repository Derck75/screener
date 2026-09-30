/* D1 emule sur node:sqlite : meme contrat HTTP que l'API Cloudflare. */
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';

export function creerBase() {
  const db = new DatabaseSync(':memory:');
  db.exec(readFileSync(new URL('../sql/001_schema.sql', import.meta.url), 'utf8'));
  // Colonnes ajoutees par les scripts eux-memes (migrer) : on les pose ici
  // pour les etapes que le test ne rejoue pas.
  for (const c of ["sic TEXT", "devise_comptes TEXT"]) db.exec(`ALTER TABLE societe ADD COLUMN ${c}`);
  return db;
}

export function installer(db, extra = {}) {
  const orig = globalThis.fetch;
  const appels = [];
  globalThis.fetch = async (u, init) => {
    const url = String(u);
    appels.push(url);
    if (url.includes('/d1/database/')) {
      const { sql, params } = JSON.parse(init.body);
      try {
        const st = db.prepare(sql);
        const ro = /^\s*(SELECT|PRAGMA)/i.test(sql);
        const res = ro ? st.all(...(params || [])) : (st.run(...(params || [])), []);
        return new Response(JSON.stringify({ success: true, result: [{ results: res, meta: { changes: 0 } }] }));
      } catch (e) {
        return new Response(JSON.stringify({ success: false, errors: [{ message: e.message, sql: sql.slice(0, 200) }] }));
      }
    }
    for (const [motif, rep] of Object.entries(extra)) if (url.includes(motif)) return new Response(rep.body, { status: rep.status || 200 });
    throw new Error('fetch inattendu ' + url);
  };
  return { appels, restaurer: () => { globalThis.fetch = orig; } };
}

/* Liaison D1 d'un worker (env.SCREENER) sur la meme base. */
export function liaison(db) {
  const stmt = (sql, p = []) => ({
    bind: (...q) => stmt(sql, q),
    first: async () => { const r = db.prepare(sql).all(...p); return r[0] || null; },
    all: async () => ({ results: db.prepare(sql).all(...p) }),
    run: async () => { db.prepare(sql).run(...p); return { success: true }; },
  });
  return { prepare: sql => stmt(sql), batch: async l => { for (const s of l) await s.run(); return []; } };
}
