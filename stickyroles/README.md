# Sticky Roles (.sr) - Red-DiscordBot

Cog separato da Quarantena. Conserva una copia persistente **per server e ID utente** dei ruoli di tutti i membri presenti, anche dei membri senza ruoli. La copia si aggiorna con i cambiamenti successivi. Quando una persona esce e rientra, ripristina i ruoli salvati e rimuove quelli gestibili che non facevano parte della copia. Non modifica i permessi dei ruoli o dei canali.

## Installazione e attivazione

Se in Red la repository e registrata con il nome `main`:

```text
.repo update main
.cog install main stickyroles
.load stickyroles
.cog update autorole quarantena reactionroles nitrorole
.reload autorole quarantena reactionroles nitrorole
.sr attiva
.sr stato
.sr conflitti
```

Nei comandi di aggiornamento/ricaricamento includi **solo i cog che hai gia installato**. `main` qui e il nome locale della repository in Downloader, non il nome del branch.

`.sr attiva` esegue la scansione completa dei membri disponibili tramite Members Intent. Il cog parte disattivato per non modificare altri server senza consenso. Dati e impostazioni persistono nel Config di Red, **non su GitHub**.

## Comandi amministrativi

- `.sr attiva`: abilita e salva tutti i membri presenti.
- `.sr disattiva`: ferma il servizio senza rimuovere ruoli Discord o eliminare le copie.
- `.sr salva`: aggiorna la scansione senza sovrascrivere i ripristini in corso o i membri usciti.
- `.sr salva @Utente`: accetta esplicitamente i ruoli attuali e interrompe un eventuale ripristino per quel membro.
- `.sr utente ID_UTENTE`: mostra copia ed esito, anche per utenti fuori dal server.
- `.sr ripristina @Utente`: riapplica la copia, eliminando i ruoli gestibili extra; utile dopo aver corretto i permessi.
- `.sr dimentica ID_UTENTE CONFERMO`: cancella solo la copia, non i ruoli attuali. Un futuro cambio ruoli o una scansione puo creare una nuova copia.
- `.sr stato`: versione, numero di copie e ripristini, permessi.
- `.sr conflitti`: controlla i cog caricati su quel Red e segnala scrittori di ruoli non integrati.

## Precedenza e compatibilita

**AutoRole**: l'adattatore chiede a Sticky Roles se si tratta di un rientro PRIMA di assegnare il ruolo. Il controllo funziona indipendentemente dall'ordine dei listener e conserva la distinzione tra primo ingresso e rientro anche se la copia e vuota. L'autorole rimane attivo per i nuovi membri.

**Quarantena**: il vecchio esperimento `quarantena/sticky.py` e rimosso. Il file principale e le sue impostazioni restano invariati. Un piccolo adattatore termina prima il ripristino dei ruoli, cosi una successiva quarantena non viene annullata da un job ancora attivo. Non vengono aggiunti comandi sticky a `.quarantena`.

**ReactionRoles / NitroRole**: le azioni esplicite sulle reazioni o sui boost vengono coordinate dopo il ripristino e poi memorizzate normalmente. Sticky Roles non congela i ruoli per tutta la permanenza e non impedisce le successive modifiche dello staff.

I controlli del rientro sono finiti (subito, dopo 2 secondi, dopo altri 5). Eventuali assegnazioni concorrenti da bot non integrati possono quindi essere corrette per circa 7 secondi, **non prima che Discord le renda effettive**. Non esiste una priorita assoluta fra bot diversi: per autorole esterni/tardivi va configurata l'esclusione dei membri di ritorno. Non vengono alterate funzioni globali di discord.py o di altri bot.

I file legacy e i moduli alternativi non caricati non sono dichiarati testati in esecuzione. `audit_repository.py` effettua un controllo AST di ogni file Python della repository (esclusi test e cache), stampando chiamate di modifica ruoli, listener ingresso ed errori sintattici. Il workflow `Sticky Roles tests and role-writer audit` lo esegue insieme ai test; `.sr conflitti` controlla invece i cog effettivamente caricati.

## Limiti necessari

- Il bot richiede **Server Members Intent** (nel Developer Portal e in Red) e **Gestire ruoli**, con il proprio ruolo sopra quelli da gestire.
- `@everyone` e implicito. I ruoli di integrazioni/booster gestiti da Discord si registrano per diagnostica ma non si forzano; neppure i ruoli eliminati o sopra il bot sono ripristinabili. Il bot stesso e il proprietario sono memorizzati ma non modificati.
- Un rejoin non e un'operazione atomica di Discord: possono esserci brevi finestre prima del ripristino. Questo cog non sostituisce controlli di accesso, verifiche o ban.
- Non puo ricostruire i ruoli di persone uscite prima del primo salvataggio, ne conoscere modifiche mai osservate mentre il bot era offline. Al riavvio riconosce un nuovo `joined_at` e conserva la copia precedente.
- Un ripristino parziale non sovrascrive la copia originale. Gli errori sono registrati e non causano tentativi infiniti; correggi i permessi e usa `.sr ripristina @Utente`.
- Nel breve intervallo di ripristino, per accettare una modifica manuale non coordinata usa `.sr salva @Utente`.

## Verifica

```sh
python -m unittest discover -s stickyroles/tests -v
python stickyroles/audit_repository.py
```

I test eseguono il motore reale con doppi di Discord e Config: primo ingresso, entrambi gli ordini dei listener, copia vuota, rientro, ruoli extra, uscita durante il ripristino, reload, errori API, ruoli gestiti/gerarchia, azioni di moderazione e isolamento fra server. Non sono test su un server Discord reale.
