# Controllo compatibilita della repository

Base analizzata: danyx64/dev-cogs, main f51ccf25158dce52747e00636c3b54c8dbb50ee6.
Il primo audit AST nella CI dfb5f3aef2c298db3afb15b1fdcf5375047f3454 ha esaminato 127 file Python di produzione, incluse le nuove integrazioni allora presenti. Nessun errore sintattico. Non e un test in esecuzione di tutti i vecchi cog.

## Scrittori di ruoli trovati e coordinati

| Cog | Comportamento | Integrazione |
| --- | --- | --- |
| AutoRole | Assegna il ruolo iniziale in on_member_join | Chiede a Sticky Roles se il membro e di ritorno, prima di ogni assegnazione iniziale |
| Quarantena | Rimuove altri ruoli e assegna quarantena | Termina il ripristino prima della moderazione; poi salva tramite gli eventi normali |
| ReactionRoles | Aggiunge/toglie ruolo per reazione | Ripristino prima dell'azione voluta dall'utente |
| NitroRole | Assegna/rimuove ruolo in base al boost | Ripristino prima della gestione del cambio boost |
| HierarchyAdmin | .ha give / .ha remove | Coordina le modifiche manuali; le letture diagnostiche non cambiano niente |
| OwnerControl | .ownerctl addrole / .ownerctl removerole | Hook prima delle due operazioni; resta owner-only |

Le classi mantengono nome, Config identifier, impostazioni e comandi preesistenti. I loro file principali non sono riscritti: le integrazioni sono sottoclassi locali in sticky_compat.py, caricate dall'__init__.py.

## Altri listener ingresso rilevati

AgeGuard, InviteManager (incluse le versioni alternative), InviteTracker, MemberCounter, ServerLogger, Status, Welcome non contenevano chiamate dirette di assegnazione/rimozione ruoli nell'audit. Non vengono modificati. AgeGuard continua a effettuare le proprie verifiche ed eventuali espulsioni; Sticky Roles non lo disabilita.

Il rapporto completo e ristampato a ogni run della CI. Il controllo AST individua chiamate dirette add_roles/remove_roles e edit(roles=): non dimostra l'assenza di ogni possibile effetto indiretto o dipendenza esterna. Il comando `.sr conflitti` esamina i cog realmente caricati su Red e segnala quelli non integrati. I bot esterni restano fuori dal controllo di questa repository.

## Cosa non significa precedenza

Non e un blocco permanente dei ruoli: dopo il rientro le modifiche volute vengono registrate. Non puo vietare preventivamente una richiesta HTTP gia mandata da un altro bot, ripristinare ruoli gestiti da Discord o saltare la gerarchia. Mantieni l'autorole esterno disabilitato per chi rientra. In caso di errore API la copia originale resta disponibile per riprovare.
