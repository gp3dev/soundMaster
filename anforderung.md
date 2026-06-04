# Datenerfassung und Home-Assistant API mit Laserliner Soundmaster

Mit einem Laserliner SoundTest-Master solle eine Langzeitdatenerfassung ermöglicht werden.
Der Soundtest-Master ist über eine Optoisolierte RS232 Schnittstelle (Chip: CP2102N-A02-GQFN28R) per USB angebunden.

Die Daten sollen mit dem aktuellen Zeitstempel in eine Datenbank gespeichert werden.

Die Erfassungs-Anwendung soll in der Linux Console laufen und während der Messung den aktuellen Wert anzeigen und einen Garfen über die letzte Minute anzeigen.

Die in der DAtenbank gespeichtern Werte sollen über eine API abrufbar sein. Die API soll später in HomeAssistant eingebunden werden. 
