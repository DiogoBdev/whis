#!/bin/zsh
# O vigia do Whis no macOS: consulta o launchd e avisa quando uma peça não subiu.
#
# Autossuficiente de propósito, como o vigia.ps1 do Windows: não chama Python,
# não lê config.toml — o que ele vigia é o Whis morrendo, e um vigia que
# dependesse do Whis morreria junto.
#
# Uso: vigia.sh <rotulo-da-bandeja> <rotulo1,rotulo2,...>

bandeja="$1"
tarefas="$2"
dominio="gui/$(id -u)"
problemas=()

for rotulo in ${(s:,:)tarefas}; do
    [[ -z "$rotulo" ]] && continue
    if ! info=$(launchctl print "$dominio/$rotulo" 2>/dev/null); then
        problemas+=("A tarefa $rotulo sumiu do launchd.")
        continue
    fi
    codigo=$(print -r -- "$info" | awk -F'= ' '/last exit code/ {print $2; exit}')
    if [[ -n "$codigo" && "$codigo" != "0" && "$codigo" != *"never exited"* ]]; then
        problemas+=("$rotulo falhou (código $codigo).")
    fi
done

bandeja_no_ar() { pgrep -f -- "-m whis bandeja" >/dev/null; }

if ! bandeja_no_ar; then
    if launchctl print "$dominio/$bandeja" >/dev/null 2>&1; then
        launchctl kickstart "$dominio/$bandeja" >/dev/null 2>&1
        sleep 20
    fi
    if bandeja_no_ar; then
        echo "A bandeja tinha caído; o vigia subiu de novo."
    else
        problemas+=("A bandeja não está no ar e não subiu.")
    fi
fi

if (( ${#problemas} == 0 )); then
    echo "Tudo de pé: $tarefas e a bandeja."
    exit 0
fi

for p in $problemas; do echo "$p"; done
texto="${(j: :)problemas}"
texto="${texto//\\/\\\\}"
texto="${texto//\"/\\\"}"
osascript -e "display notification \"$texto\" with title \"Whis\" subtitle \"O vigia achou um problema\""
exit 1
