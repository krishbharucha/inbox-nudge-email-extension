import React from 'react';
import ReactDOM from 'react-dom/client';
import './styles.css';
import App from './App';
import EmailPreferences from './EmailPreferences';
import ExtensionApp from './ExtensionApp';

if (typeof chrome !== 'undefined' && chrome.runtime?.id && !location.search.includes('popup')) {
  document.documentElement.classList.add('extension-workspace');
}
if (location.search.includes('popup')) document.documentElement.classList.add('popup');

const emailPreferenceToken = new URLSearchParams(location.search).get('email_preferences');
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {emailPreferenceToken ? (
      <EmailPreferences token={emailPreferenceToken} />
    ) : typeof chrome !== 'undefined' && chrome.runtime?.id ? (
      <ExtensionApp />
    ) : (
      <App />
    )}
  </React.StrictMode>,
);
