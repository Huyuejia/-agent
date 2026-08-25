import { useState, useRef, useEffect } from 'react';
import './App.css';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface MessageSource {
  source_type: 'knowledge_graph' | 'document_rag';
  document_name?: string | null;
  location?: string | null;
  snippet?: string | null;
  relation?: string | null;
}

interface ChatResponse {
  conversation_id: number;
  answer: string;
  intent: string;
  confidence: number;
  source_type: string;
  sources: MessageSource[];
  handoff_required: boolean;
}

interface Message {
  role: 'user' | 'assistant';
  content: string;
  detail?: ChatResponse;
}

const API_BASE = 'http://localhost:8000';

const EXAMPLES = [
  { label: '兼容', text: 'Cam-A1和Hub-Z1兼容吗' },
  { label: '协议', text: 'Hub-Z1支持什么协议' },
  { label: '保修', text: 'Cam-A1保修多久' },
  { label: '退货', text: '怎么退货' },
  { label: '天气', text: '今天天气怎么样' },
];

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '发生未知错误';
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------
export default function App() {
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);


  function clearSession() {
    setAccessToken(null);
    setConversationId(null);
    setMessages([]);
    setPassword('');
  }

  async function handleLogin(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `登录失败 (${res.status})`);
      }
      const data = await res.json();
      setAccessToken(data.access_token);
      setPassword('');
    } catch (error: unknown) {
      setError(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }

  function handleUnauthorized() {
    clearSession();
    setError('登录已过期，请重新登录');
  }

  function handleLogout() {
    clearSession();
    setError(null);
  }
  // ---- new conversation ----
  async function handleNewConversation() {
    if (!accessToken) return;
    setError(null);
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/conversations?title=客服会话`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (res.status === 401) {
        handleUnauthorized();
        return;
      }
      if (!res.ok) throw new Error(`创建会话失败 (${res.status})`);
      const data = await res.json();
      setConversationId(data.conversation_id);
      setMessages([]);
    } catch (error: unknown) {
      setError(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }

  // ---- send message ----
  async function sendMessage(text: string) {
    if (!accessToken || !conversationId || !text.trim()) return;
    setError(null);
    const userMsg: Message = { role: 'user', content: text };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setLoading(true);

    try {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({ conversation_id: conversationId, message: text }),
      });
      if (res.status === 401) {
        handleUnauthorized();
        return;
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `请求失败 (${res.status})`);
      }
      const data: ChatResponse = await res.json();
      const assistantMsg: Message = { role: 'assistant', content: data.answer, detail: data };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch (error: unknown) {
      setError(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  }

  // ---- render ----
  if (!accessToken) {
    return (
      <div className="auth-page">
        <form className="auth-card" onSubmit={handleLogin}>
          <div className="auth-kicker">Customer Intelligence Workbench</div>
          <h1>登录智能客服工作台</h1>
          <p>Access Token 仅保存在当前页面内存中，刷新页面后需要重新登录。</p>
          {error && <div className="wb-error">{error}</div>}
          <label>
            邮箱
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="username"
              required
            />
          </label>
          <label>
            密码
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              minLength={8}
              required
            />
          </label>
          <button className="wb-btn auth-submit" type="submit" disabled={loading}>
            {loading ? '登录中…' : '登录'}
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="workbench">
      {/* header */}
      <header className="wb-header">
        <h1 className="wb-title">智能客服工作台</h1>
        <button
          className="wb-btn wb-btn-new"
          onClick={handleNewConversation}
          disabled={loading}
        >
          新建会话
        </button>
        <button
          className="wb-btn wb-btn-logout"
          onClick={handleLogout}
          disabled={loading}
        >
          退出
        </button>
      </header>

      {/* error banner */}
      {error && (
        <div className="wb-error">
          <span>{error}</span>
          <button className="wb-error-close" onClick={() => setError(null)}>
            &times;
          </button>
        </div>
      )}

      {/* message area */}
      <div className="wb-messages">
        {!conversationId && !loading && (
          <div className="wb-placeholder">点击「新建会话」开始</div>
        )}
        {conversationId && messages.length === 0 && !loading && (
          <div className="wb-placeholder">输入问题开始对话</div>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`wb-msg ${msg.role}`}>
            <div className="wb-msg-label">{msg.role === 'user' ? '用户' : '助手'}</div>
            <div className="wb-msg-content">{msg.content}</div>
            {msg.detail && (
              <div className="wb-msg-meta">
                <span className="wb-tag intent">{msg.detail.intent}</span>
                <span className="wb-tag confidence">
                  {(msg.detail.confidence * 100).toFixed(0)}%
                </span>
                <span className="wb-tag source">{msg.detail.source_type}</span>
                {msg.detail.handoff_required && (
                  <span className="wb-tag handoff">建议人工跟进</span>
                )}
                {msg.detail.sources.length > 0 && (
                  <div className="wb-sources">
                    {msg.detail.sources.map((s, si) => (
                      <div key={si} className="wb-source-item">
                        {s.source_type === 'knowledge_graph' && s.relation && (
                          <span className="wb-source-rel">{s.relation}</span>
                        )}
                        {s.source_type === 'document_rag' && (
                          <>
                            {s.document_name && (
                              <span className="wb-source-doc">{s.document_name}</span>
                            )}
                            {s.location && (
                              <span className="wb-source-loc">{s.location}</span>
                            )}
                            {s.snippet && (
                              <span className="wb-source-snippet">{s.snippet}</span>
                            )}
                          </>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
        {loading && <div className="wb-loading">处理中...</div>}
        <div ref={bottomRef} />
      </div>

      {/* input area */}
      <div className="wb-input-area">
        <div className="wb-examples">
          {EXAMPLES.map((ex) => (
            <button
              key={ex.label}
              className="wb-btn wb-btn-example"
              disabled={!conversationId || loading}
              onClick={() => sendMessage(ex.text)}
            >
              {ex.label}
            </button>
          ))}
        </div>
        <div className="wb-input-row">
          <input
            className="wb-input"
            type="text"
            placeholder={conversationId ? '输入问题...' : '请先创建会话'}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={!conversationId || loading}
          />
          <button
            className="wb-btn wb-btn-send"
            disabled={!conversationId || loading || !input.trim()}
            onClick={() => sendMessage(input)}
          >
            发送
          </button>
        </div>
      </div>
    </div>
  );
}
