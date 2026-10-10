#!/bin/bash

GROQ_URL="https://api.groq.com/openai/v1"
OPENROUTER_URL="https://openrouter.ai/api/v1"

# Dossier contenant les benchmarks
BENCH_DIR="benchmark"

# Liste ordonnée des dossiers de modèles à évaluer
AVAILABLE_MODELS=(
    "gemma-4-31b-it"
    "gpt-os-120b"
    "nemotron-3-super-120b-a12b"
    "north-mini-code"
    "qwen3.8-27b"
)

echo "=========================================================="
echo "🎯 Sélection des modèles pour le SWE-bench"
echo "=========================================================="
echo "0) Tous les modèles"
for i in "${!AVAILABLE_MODELS[@]}"; do
    echo "$((i+1))) ${AVAILABLE_MODELS[$i]}"
done
echo "=========================================================="
read -p "Entrez les numéros des modèles à lancer (ex: 1 3 5) : " -a user_choices

# Si l'utilisateur n'a rien saisi, on quitte
if [ ${#user_choices[@]} -eq 0 ]; then
    echo "❌ Aucune sélection. Fin du script."
    exit 0
fi

# Résolution des modèles choisis
SELECTED_MODELS=()
if [[ " ${user_choices[*]} " == *" 0 "* ]]; then
    SELECTED_MODELS=("${AVAILABLE_MODELS[@]}")
else
    for choice in "${user_choices[@]}"; do
        # Vérification que le choix correspond bien à un index valide
        if [[ "$choice" =~ ^[1-5]$ ]]; then
            SELECTED_MODELS+=("${AVAILABLE_MODELS[$((choice-1))]}")
        else
            echo "⚠️  Choix '$choice' ignoré (invalide)."
        fi
    done
fi

# Vérification finale qu'au moins un modèle valide est sélectionné
if [ ${#SELECTED_MODELS[@]} -eq 0 ]; then
    echo "❌ Aucun modèle valide sélectionné. Fin du script."
    exit 0
fi

echo ""
echo "🚀 Modèle(s) retenu(s) pour cette session :"
for mod in "${SELECTED_MODELS[@]}"; do echo "  - $mod"; done
echo ""

# Boucle principale sur les modèles sélectionnés
for model_dir in "${SELECTED_MODELS[@]}"; do
    model_dir_path="$BENCH_DIR/$model_dir/"
    
    # Éviter les erreurs si le dossier n'existe physiquement pas
    if [ ! -d "$model_dir_path" ]; then
        echo "⚠️  Le dossier $model_dir_path est introuvable, passage au suivant..."
        continue
    fi

    # Détermination dynamique du provider-url et du model-name correct
    case "$model_dir" in
        "gpt-os-120b")
            PROVIDER_URL="$GROQ_URL"
            MODEL_NAME="openai/gpt-oss-120b"
            ;;
        "qwen3.8-27b")
            PROVIDER_URL="$GROQ_URL"
            MODEL_NAME="qwen/qwen3.8-27b"
            ;;
        "gemma-4-31b-it")
            PROVIDER_URL="$OPENROUTER_URL"
            MODEL_NAME="google/gemma-4-31b-it:free"
            ;;
        "nemotron-3-super-120b-a12b")
            PROVIDER_URL="$OPENROUTER_URL"
            MODEL_NAME="nvidia/nemotron-3-super-120b-a12b:free"
            ;;
        "north-mini-code")
            PROVIDER_URL="$OPENROUTER_URL"
            MODEL_NAME="cohere/north-mini-code:free"
            ;;
    esac

    echo "=========================================================="
    echo "🤖 Début de l'évaluation pour : $MODEL_NAME"
    echo "🌐 Fournisseur : $PROVIDER_URL"
    echo "=========================================================="

    # Parcourir tous les fichiers JSON de tâches à l'intérieur de ce dossier
    for task_file_path in "$model_dir_path"*.json; do
        [ -f "$task_file_path" ] || continue
        
        # Ignorer les fichiers de solution déjà présents
        if [[ "$(basename "$task_file_path")" == solution_* ]]; then
            continue
        fi

        task_file_name=$(basename "$task_file_path")
        task="${task_file_name%.json}"

        # Sauvegarde au même endroit à côté de la tâche
        output_file="${model_dir_path}solution_${task}.json"

        echo "🚀 Exécution de la tâche : $task"

        # Lancement de la commande avec uv
        uv run python -m agent_swebench \
            --env-file .env \
            --task-file "$task_file_path" \
            --output "$output_file" \
            --model-name "$MODEL_NAME" \
            --provider-url "$PROVIDER_URL"

        echo "✅ Tâche terminée. Résultat sauvegardé dans $output_file"
        echo "----------------------------------------------------------"
    done
done

echo "🎉 Évaluations terminées pour la sélection !"

