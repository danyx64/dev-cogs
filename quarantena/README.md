# Quarantena - Red-DiscordBot

Cog di moderazione automatica per RedBot, compatibile con discord.py 2.x.

## Installazione
```text
.repo update main
.cog install main quarantena
.load quarantena
```

## Configurazione
```text
.quarantena ruolo ID_RUOLO
.quarantena aggiungi parola
.quarantena rimuovi parola
.quarantena lista
.quarantena blocca ID_RUOLO ID_CANALE
.quarantena attiva
.quarantena disattiva
.quarantena stato
```

Controlla nuovi messaggi, modifiche ai messaggi, nomi all'ingresso e cambi di nickname. In caso di violazione assegna un ruolo, prova a impostare il nickname UserN e cancella i messaggi accessibili nei canali testuali e thread attivi.

Nel Discord Developer Portal abilita Server Members Intent e Message Content Intent. Il bot richiede Manage Roles, Manage Nicknames, Manage Messages, Read Message History e i permessi per gestire gli overwrite sul canale. Posiziona il ruolo del bot sopra i ruoli coinvolti.

La cancellazione dei messaggi e irreversibile; il controllo retroattivo esamina tutta la cronologia visibile e potrebbe richiedere tempo. Altri ruoli possono mantenere autorizzazioni anche dopo la negazione dei permessi a un ruolo specifico. Il cog parte disattivato finche non viene configurato e attivato.
