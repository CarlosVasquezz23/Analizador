"""Ticket entry, OCR review and results for the verifier."""
import hashlib
import math

import pandas as pd
import streamlit as st

from analytics import parse_ticket

EXAMPLE = "Real Madrid 1.50\nArsenal 1.80\nInter 1.65"
MAX_SELECTIONS = 20
SIMULATIONS = 10_000


def extract_text(upload):
    from PIL import Image
    import pytesseract

    with Image.open(upload) as image:
        if image.width * image.height > 16_000_000:
            raise ValueError("La imagen supera los 16 megapíxeles. Recorta la captura y vuelve a subirla.")
        available = pytesseract.get_languages(config="")
        languages = "+".join(lang for lang in ("spa", "eng") if lang in available)
        if not languages:
            raise RuntimeError("Falta un idioma compatible de Tesseract.")
        return pytesseract.image_to_string(image, lang=languages, timeout=15).strip()


def photo_input():
    upload = st.file_uploader("Captura del boleto", type=["jpg", "jpeg", "png"])
    if upload is None:
        st.session_state.pop("verifier_image_id", None)
        st.session_state.verifier_ocr = ""
        return None, ""
    image_id = hashlib.sha256(upload.getbuffer()).hexdigest()
    if st.session_state.get("verifier_image_id") != image_id:
        st.session_state.verifier_image_id = image_id
        st.session_state.verifier_ocr = ""

    if st.button("Extraer texto", icon=":material/document_scanner:"):
        try:
            with st.spinner("Leyendo la captura…"):
                extracted = extract_text(upload)
            if extracted:
                st.session_state.verifier_ocr = extracted
            else:
                st.warning("No se detectó texto. Prueba con una captura más nítida o pega las selecciones.")
        except ValueError as error:
            st.error(str(error))
        except Exception:
            st.error("No se pudo leer la captura. Prueba otra imagen o pega las selecciones.")

    text = st.text_area(
        "Revisa el texto de la captura", key="verifier_ocr", height=155,
        help="Conserva una selección y su cuota decimal por línea. Corrige los errores de lectura antes de verificar.",
    )
    return image_id, text


def validate_ticket(text):
    lines = [(number, line) for number, line in enumerate(text.splitlines(), 1) if line.strip()]
    if not lines:
        raise ValueError("Escribe al menos una selección con su cuota decimal.")
    if len(lines) > MAX_SELECTIONS:
        raise ValueError(f"El boleto admite hasta {MAX_SELECTIONS} selecciones.")
    selections = []
    invalid = []
    for number, line in lines:
        parsed = parse_ticket(line.strip())
        if parsed:
            selections.extend(parsed)
        else:
            invalid.append(str(number))
    if invalid:
        numbers = ", ".join(invalid)
        raise ValueError(f"Revisa estas líneas: {numbers}. Cada una debe terminar en una cuota decimal mayor que 1.")
    return selections


def render_results(selections, simulate):
    combined_odds = math.prod(p["cuota"] for p in selections)
    probability = math.prod(p["prob_real"] / 100 for p in selections)
    expected_return = math.expm1(len(selections) * math.log(0.95))
    if not math.isfinite(combined_odds):
        st.error("La cuota combinada es demasiado grande. Revisa las cuotas del boleto.")
        return

    st.subheader("Resultado del boleto")
    with st.container(key="verifier_metrics"):
        odds, chance, ev = st.columns(3)
        odds.metric("Cuota combinada", f"{combined_odds:,.2f}")
        chance.metric("Acierto estimado", f"{probability:.2%}")
        ev.metric("Retorno esperado", f"{expected_return:+.1%}",
                  help="Ganancia o pérdida media estimada por unidad apostada, según el supuesto del verificador.")
    st.caption("Estimación a partir de las cuotas con un ajuste fijo del 5 % por selección. La combinada supone que las selecciones son independientes.")

    frame = pd.DataFrame([
        {"Selección": p["nombre"], "Cuota": p["cuota"], "Acierto estimado (%)": p["prob_real"]}
        for p in selections
    ])
    st.dataframe(frame, hide_index=True, width="stretch", column_config={
        "Cuota": st.column_config.NumberColumn(format="%.2f"),
        "Acierto estimado (%)": st.column_config.NumberColumn(format="%.1f%%"),
    })
    with st.expander("Ver simulación de 10.000 boletos"):
        results = simulate(selections, SIMULATIONS)
        full, near = st.columns(2)
        full.metric("Aciertan todas", f"{results['pleno_acierto']:.1f}%")
        near.metric("Falla una selección", f"{results['fallo_por_uno']:.1f}%")
        st.caption("La simulación usa las mismas probabilidades estimadas a partir de las cuotas.")


def render_verifier(simulate):
    st.title("Verifica tu boleto")
    st.caption("Pega tus selecciones o sube una captura para calcular la cuota combinada y revisar el riesgo estimado.")

    # Keep both drafts when Streamlit removes the widgets for the inactive input method.
    for key in ("verifier_manual", "verifier_ocr"):
        st.session_state[key] = st.session_state.get(key, "")

    with st.container(border=True, key="verifier_entry"):
        method = st.radio("Método de entrada", ["Pegar texto", "Subir captura"], horizontal=True)
        if method == "Pegar texto":
            st.button("Cargar ejemplo", on_click=lambda: st.session_state.update(verifier_manual=EXAMPLE))
            text = st.text_area("Selecciones del boleto", key="verifier_manual", placeholder=EXAMPLE, height=155,
                                help="Escribe una selección por línea y termina cada línea con su cuota decimal.")
            source = "manual"
        else:
            source, text = photo_input()
        analyze = st.button("Verificar boleto", type="primary", width="stretch", disabled=not text.strip())

    if analyze:
        st.session_state.pop("verifier_result", None)
        try:
            selections = validate_ticket(text)
        except ValueError as error:
            st.error(str(error))
        else:
            st.session_state.verifier_result = (source, text, selections)

    result = st.session_state.get("verifier_result")
    if result and result[:2] == (source, text):
        render_results(result[2], simulate)
    elif result and text.strip():
        st.info("Pulsa Verificar boleto para calcular el resultado con la entrada actual.")
