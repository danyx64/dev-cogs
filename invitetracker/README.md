# InviteTracker (Italiano)

Versione italiana compatibile con il cog `invitetracker` di `TaakoOfficial/TaakosCogs`.

## Compatibilita dati

Questa versione mantiene intenzionalmente:

- cartella `invitetracker`
- classe `InviteTracker`
- Config identifier `2026051302`
- chiavi guild `enabled`, `log_channel_id`, `include_bots`, `fake_age_hours`, `invite_cache`, `inviters`, `members`, `unknown_joins`
- struttura dei record di inviti, invitatori e membri

Di conseguenza Red puo riutilizzare direttamente la Config gia salvata dal cog originale senza una migrazione dati separata.

## Funzioni

- traccia quale invito e stato probabilmente usato all'ingresso;
- registra invitatori e conteggi ingresso/uscita/fake;
- rileva join fake in base all'eta dell'account;
- registra le uscite;
- mostra classifica e statistiche per invitante;
- mostra la sorgente di ingresso di un membro;
- esporta i record in CSV;
- supporta Red-Web-Dashboard;
- mantiene i comandi originali e aggiunge alias italiani.

## Comandi principali

```text
[p]invitetracker status
[p]invitetracker setup #canale
[p]invitetracker attiva
[p]invitetracker disattiva
[p]invitetracker canale #canale
[p]invitetracker etafake 24
[p]invitetracker includibot true
[p]invitetracker aggiorna
[p]invitetracker resetstatistiche conferma

[p]inviti
[p]inviti classifica 10
[p]inviti fonte @utente
[p]inviti invitati @utente
[p]inviti esporta
```

## Nota sul rilevamento inviti

Discord non comunica direttamente ai bot quale invito e stato usato da un membro. InviteTracker confronta i contatori di utilizzo degli inviti prima e dopo l'ingresso, come il cog originale.

## Licenza

Derivato da `TaakoOfficial/TaakosCogs`, distribuito sotto GNU Affero General Public License v3.0 (AGPL-3.0). Vedi `NOTICE.md`.
