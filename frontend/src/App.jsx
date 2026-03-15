import { useState, useEffect } from 'react';
import Sidebar from './components/Sidebar';
import ChatInterface from './components/ChatInterface';
import Login from './components/Login';
import { api } from './api';
import './App.css';

function App() {
  const [authenticated, setAuthenticated] = useState(null); // null = checking
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(() => {
    const hash = window.location.hash.slice(1);
    return hash || null;
  });
  const [currentConversation, setCurrentConversation] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [followUpModel, setFollowUpModel] = useState(null);

  // Sync hash to state on back/forward navigation
  useEffect(() => {
    const onHashChange = () => {
      const hash = window.location.hash.slice(1);
      setCurrentConversationId(hash || null);
    };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  // Check auth on mount
  useEffect(() => {
    api.checkAuth().then((res) => {
      setAuthenticated(res.authenticated || !res.auth_enabled);
    }).catch(() => setAuthenticated(false));
  }, []);

  // Load conversations on mount (only when authenticated)
  useEffect(() => {
    if (authenticated) loadConversations();
  }, [authenticated]);

  // Load conversation details when selected
  useEffect(() => {
    if (currentConversationId) {
      loadConversation(currentConversationId);
    }
  }, [currentConversationId]);

  const loadConversations = async () => {
    try {
      const convs = await api.listConversations();
      setConversations(convs);
    } catch (error) {
      console.error('Failed to load conversations:', error);
    }
  };

  const loadConversation = async (id) => {
    try {
      const conv = await api.getConversation(id);
      setCurrentConversation(conv);
    } catch (error) {
      console.error('Failed to load conversation:', error);
    }
  };

  const handleNewConversation = async () => {
    try {
      const newConv = await api.createConversation();
      setConversations([
        { id: newConv.id, created_at: newConv.created_at, message_count: 0 },
        ...conversations,
      ]);
      window.location.hash = newConv.id;
      setCurrentConversationId(newConv.id);
    } catch (error) {
      console.error('Failed to create conversation:', error);
    }
  };

  const handleSelectConversation = (id) => {
    window.location.hash = id;
    setCurrentConversationId(id);
  };

  const handleLogin = async (username, password) => {
    await api.login(username, password);
    setAuthenticated(true);
  };

  const handleLogout = async () => {
    await api.logout();
    setAuthenticated(false);
  };

  if (authenticated === null) return null; // checking auth
  if (!authenticated) return <Login onLogin={handleLogin} />;

  const handleSendMessage = async (content) => {
    if (!currentConversationId) return;

    setIsLoading(true);
    try {
      // Optimistically add user message to UI
      const userMessage = { role: 'user', content };
      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, userMessage],
      }));

      // Create a partial assistant message that will be updated progressively
      const assistantMessage = {
        role: 'assistant',
        stage1: null,
        stage2: null,
        stage3: null,
        metadata: null,
        loading: {
          stage1: false,
          stage2: false,
          stage3: false,
        },
      };

      // Add the partial assistant message
      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, assistantMessage],
      }));

      // Send message with streaming
      await api.sendMessageStream(currentConversationId, content, (eventType, event) => {
        switch (eventType) {
          case 'stage1_start':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.loading.stage1 = true;
              // Initialize streaming state with model list
              lastMsg.stage1 = (event.models || []).map((m) => ({
                model: m,
                response: '',
                streaming: true,
              }));
              return { ...prev, messages };
            });
            break;

          case 'stage1_model_token':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              if (lastMsg.stage1) {
                const modelEntry = lastMsg.stage1.find((r) => r.model === event.model);
                if (modelEntry) modelEntry.response += event.token;
              }
              return { ...prev, messages };
            });
            break;

          case 'stage1_model_complete':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              if (lastMsg.stage1) {
                const modelEntry = lastMsg.stage1.find((r) => r.model === event.model);
                if (modelEntry) {
                  modelEntry.response = event.content;
                  modelEntry.streaming = false;
                }
              }
              return { ...prev, messages };
            });
            break;

          case 'stage1_model_error':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              if (lastMsg.stage1) {
                lastMsg.stage1 = lastMsg.stage1.filter((r) => r.model !== event.model);
              }
              return { ...prev, messages };
            });
            break;

          case 'stage1_complete':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.loading.stage1 = false;
              // Remove any entries with empty responses (failed models)
              if (lastMsg.stage1) {
                lastMsg.stage1 = lastMsg.stage1.filter((r) => r.response);
              }
              return { ...prev, messages };
            });
            break;

          case 'stage2_start':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.loading.stage2 = true;
              lastMsg.stage2 = (event.models || []).map((m) => ({
                model: m,
                ranking: '',
                parsed_ranking: [],
                streaming: true,
              }));
              return { ...prev, messages };
            });
            break;

          case 'stage2_model_token':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              if (lastMsg.stage2) {
                const modelEntry = lastMsg.stage2.find((r) => r.model === event.model);
                if (modelEntry) modelEntry.ranking += event.token;
              }
              return { ...prev, messages };
            });
            break;

          case 'stage2_model_complete':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              if (lastMsg.stage2) {
                const modelEntry = lastMsg.stage2.find((r) => r.model === event.model);
                if (modelEntry) {
                  modelEntry.ranking = event.content;
                  modelEntry.streaming = false;
                }
              }
              return { ...prev, messages };
            });
            break;

          case 'stage2_model_error':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              if (lastMsg.stage2) {
                lastMsg.stage2 = lastMsg.stage2.filter((r) => r.model !== event.model);
              }
              return { ...prev, messages };
            });
            break;

          case 'stage2_complete':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.loading.stage2 = false;
              lastMsg.metadata = event.metadata;
              // Remove entries with empty rankings
              if (lastMsg.stage2) {
                lastMsg.stage2 = lastMsg.stage2.filter((r) => r.ranking);
              }
              return { ...prev, messages };
            });
            break;

          case 'stage3_start':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.loading.stage3 = true;
              lastMsg.stage3 = { model: event.model, response: '', streaming: true };
              return { ...prev, messages };
            });
            break;

          case 'stage3_token':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              if (lastMsg.stage3) {
                lastMsg.stage3.response += event.token;
              }
              return { ...prev, messages };
            });
            break;

          case 'stage3_complete':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.stage3 = event.data;
              lastMsg.loading.stage3 = false;
              return { ...prev, messages };
            });
            break;

          case 'title_complete':
            loadConversations();
            break;

          case 'complete':
            loadConversations();
            setIsLoading(false);
            break;

          case 'error':
            console.error('Stream error:', event.message);
            setIsLoading(false);
            break;

          default:
            console.log('Unknown event type:', eventType);
        }
      });
    } catch (error) {
      console.error('Failed to send message:', error);
      // Remove optimistic messages on error
      setCurrentConversation((prev) => ({
        ...prev,
        messages: prev.messages.slice(0, -2),
      }));
      setIsLoading(false);
    }
  };

  const handleFollowUp = async (content) => {
    if (!currentConversationId || !followUpModel) return;

    setIsLoading(true);
    try {
      // Add user follow-up message to UI
      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages,
          { role: 'user', content, followup: true },
          { role: 'assistant', content: '', model: followUpModel, followup: true, streaming: true },
        ],
      }));

      await api.sendFollowUpStream(currentConversationId, content, followUpModel, (eventType, event) => {
        switch (eventType) {
          case 'followup_token':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              if (lastMsg.followup) lastMsg.content += event.token;
              return { ...prev, messages };
            });
            break;

          case 'followup_complete':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              if (lastMsg.followup) {
                lastMsg.content = event.content;
                lastMsg.streaming = false;
              }
              return { ...prev, messages };
            });
            setIsLoading(false);
            break;

          case 'error':
            console.error('Follow-up error:', event.message);
            setIsLoading(false);
            break;
        }
      });
    } catch (error) {
      console.error('Failed to send follow-up:', error);
      setCurrentConversation((prev) => ({
        ...prev,
        messages: prev.messages.slice(0, -2),
      }));
      setIsLoading(false);
    }
  };

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
      />
      <ChatInterface
        conversation={currentConversation}
        onSendMessage={handleSendMessage}
        onFollowUp={handleFollowUp}
        followUpModel={followUpModel}
        onSelectFollowUpModel={setFollowUpModel}
        isLoading={isLoading}
      />
    </div>
  );
}

export default App;
