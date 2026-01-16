import os
import time
import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")

st.set_page_config(page_title="Second Brain", layout="wide")
st.title("Second Brain - Ingest + Chat")
st.caption(f"BACKEND_URL = {BACKEND_URL}")

def poll_item(item_id: str, max_wait_s: int = 180):
    """
    Poll item status until READY/FAILED or timeout.
    """
    start = time.time()
    last = None
    while time.time() - start < max_wait_s:
        r = requests.get(f"{BACKEND_URL}/items/{item_id}", timeout=10)
        data = r.json()
        last = data
        if data.get("status") in ("READY", "FAILED"):
            return data
        time.sleep(2)
    return last or {"error": "poll_timeout"}

tab1, tab2 = st.tabs(["Ingest", "Chat"])

with tab1:
    st.subheader("Ingestion")

    c1, c2 = st.columns(2)

    with c1:
        st.markdown("### Upload Audio (.mp3/.m4a/.wav)")
        audio_file = st.file_uploader("Audio", type=["mp3", "m4a", "wav"], key="audio")
        if st.button("Ingest Audio") and audio_file is not None:
            files = {"file": (audio_file.name, audio_file.getvalue())}
            resp = requests.post(f"{BACKEND_URL}/ingest/audio", files=files, timeout=60)
            data = resp.json()
            st.write("Submitted:", data)
            final = poll_item(data["item_id"])
            st.write("Final:", final)

    with c2:
        st.markdown("### Ingest Web URL")
        url = st.text_input("URL", placeholder="https://example.com/article")
        if st.button("Ingest URL") and url:
            resp = requests.post(f"{BACKEND_URL}/ingest/web", json={"url": url}, timeout=30)
            data = resp.json()
            st.write("Submitted:", data)
            final = poll_item(data["item_id"])
            st.write("Final:", final)

    st.divider()

    c3, c4 = st.columns(2)

    with c3:
        st.markdown("### Upload Document (.pdf / .md)")
        doc_file = st.file_uploader("Document", type=["pdf", "md"], key="doc")
        if st.button("Ingest Document") and doc_file is not None:
            files = {"file": (doc_file.name, doc_file.getvalue())}
            resp = requests.post(f"{BACKEND_URL}/ingest/document", files=files, timeout=60)
            data = resp.json()
            st.write("Submitted:", data)
            if "item_id" in data:
                final = poll_item(data["item_id"])
                st.write("Final:", final)

    with c4:
        st.markdown("### Upload Image (searchable via metadata text)")
        img_file = st.file_uploader("Image", type=["png", "jpg", "jpeg", "webp"], key="img")
        img_title = st.text_input("Image title", value="Image", key="img_title")
        img_tags = st.text_input("Tags (comma-separated)", value="", key="img_tags")
        img_desc = st.text_area("Description text", value="", key="img_desc")
        if st.button("Ingest Image") and img_file is not None:
            files = {"file": (img_file.name, img_file.getvalue())}
            data = {
                "title": img_title,
                "tags": img_tags,
                "description_text": img_desc,
            }
            resp = requests.post(f"{BACKEND_URL}/ingest/image", files=files, data=data, timeout=60)
            out = resp.json()
            st.write("Submitted:", out)
            if "item_id" in out:
                final = poll_item(out["item_id"])
                st.write("Final:", final)

with tab2:
    st.subheader("Chat")

    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    # Render chat history
    for m in st.session_state["messages"]:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            if m.get("citations"):
                with st.expander("Citations"):
                    for c in m["citations"]:
                        st.write("-", c["citation"])

    q = st.chat_input("Ask (e.g., 'Summarize the main points from the URL' or 'What are the key points from the PDF?')")

    if q:
        st.session_state["messages"].append({"role": "user", "content": q})
        with st.chat_message("user"):
            st.markdown(q)

        with st.chat_message("assistant"):
            placeholder = st.empty()
            placeholder.markdown("Thinking...")

            resp = requests.post(f"{BACKEND_URL}/chat", json={"query": q}, timeout=180)
            data = resp.json()

            answer = data.get("answer", "")
            citations = data.get("citations", [])

            placeholder.markdown(answer)
            if citations:
                with st.expander("Citations"):
                    for c in citations:
                        st.write("-", c["citation"])

        st.session_state["messages"].append({"role": "assistant", "content": answer, "citations": citations})
