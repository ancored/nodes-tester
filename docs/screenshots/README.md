**English** | [Русский](README.ru.md)

# nodes-tester web admin panel

[About the project](../../README.md) · [User guide](../USAGE.md) ·
[How the dashboard works](../../dashboard/README.md)

The images switch automatically between the GitHub light and dark themes. Click a
screenshot to open it in full size.

The gallery shows the 0.11.0 interface on demo data; the Russian interface is in the [Russian gallery](README.ru.md). The current workflow is described in the [guide](../USAGE.md).

## Main screens

<table>
  <tr>
    <td width="50%">
      <a href="overview-light.png">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="overview.png">
          <source media="(prefers-color-scheme: light)" srcset="overview-light.png">
          <img alt="System state overview" src="overview-light.png">
        </picture>
      </a>
    </td>
    <td width="50%">
      <a href="nodes-light.png">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="nodes.png">
          <source media="(prefers-color-scheme: light)" srcset="nodes-light.png">
          <img alt="All nodes" src="nodes-light.png">
        </picture>
      </a>
    </td>
  </tr>
  <tr>
    <td><strong>Overview</strong><br>Database and tester state, summary figures and the chosen nodes of the groups.</td>
    <td><strong>All nodes</strong><br>Filters, search, sorting, rating and a link to the card.</td>
  </tr>
  <tr>
    <td>
      <a href="node-card-light.png">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="node-card.png">
          <source media="(prefers-color-scheme: light)" srcset="node-card-light.png">
          <img alt="Node card" src="node-card-light.png">
        </picture>
      </a>
    </td>
    <td>
      <a href="rating-light.png">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="rating.png">
          <source media="(prefers-color-scheme: light)" srcset="rating-light.png">
          <img alt="Node rating" src="rating-light.png">
        </picture>
      </a>
    </td>
  </tr>
  <tr>
    <td><strong>Node card</strong><br>State, measurements, traffic, events and actions in one place.</td>
    <td><strong>Rating</strong><br>The total score and the normalised components by group.</td>
  </tr>
  <tr>
    <td>
      <a href="results-light.png">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="results.png">
          <source media="(prefers-color-scheme: light)" srcset="results-light.png">
          <img alt="Network check results" src="results-light.png">
        </picture>
      </a>
    </td>
    <td>
      <a href="lifecycle-light.png">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="lifecycle.png">
          <source media="(prefers-color-scheme: light)" srcset="lifecycle-light.png">
          <img alt="Restrictions and quality" src="lifecycle-light.png">
        </picture>
      </a>
    </td>
  </tr>
  <tr>
    <td><strong>Results</strong><br>Physical metrics with the time of each measurement, the exit IP type and access to Google AI, OpenAI and Claude.</td>
    <td><strong>Restrictions and quality</strong><br>Pauses, quarantine and a summary by provider.</td>
  </tr>
  <tr>
    <td>
      <a href="history-light.png">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="history.png">
          <source media="(prefers-color-scheme: light)" srcset="history-light.png">
          <img alt="Switch history" src="history-light.png">
        </picture>
      </a>
    </td>
    <td>
      <a href="traffic-light.png">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="traffic.png">
          <source media="(prefers-color-scheme: light)" srcset="traffic-light.png">
          <img alt="Traffic statistics" src="traffic-light.png">
        </picture>
      </a>
    </td>
  </tr>
  <tr>
    <td><strong>Switches</strong><br>When, why and to which node a group switched.</td>
    <td><strong>Traffic</strong><br>The available period, directions and the most used nodes.</td>
  </tr>
</table>

## Configuration and control

<table>
  <tr>
    <td width="50%">
      <a href="subscriptions-light.png">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="subscriptions.png">
          <source media="(prefers-color-scheme: light)" srcset="subscriptions-light.png">
          <img alt="Subscriptions and build" src="subscriptions-light.png">
        </picture>
      </a>
    </td>
    <td width="50%">
      <a href="config-light.png">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="config.png">
          <source media="(prefers-color-scheme: light)" srcset="config-light.png">
          <img alt="nodes-tester settings" src="config-light.png">
        </picture>
      </a>
    </td>
  </tr>
  <tr>
    <td><strong>Subscriptions and build</strong><br>Sources, secrets, forms and the advanced JSON.</td>
    <td><strong>Settings</strong><br>sing-box API, SOCKS, checks, switching, storage and dashboard.</td>
  </tr>
  <tr>
    <td colspan="2">
      <a href="runs-light.png">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="runs.png">
          <source media="(prefers-color-scheme: light)" srcset="runs-light.png">
          <img alt="Check progress and the live log" src="runs-light.png">
        </picture>
      </a>
    </td>
  </tr>
  <tr>
    <td colspan="2"><strong>Checks</strong><br>Tester stage, queue, the last result and the live log.</td>
  </tr>
  <tr>
    <td colspan="2">
      <a href="pipeline-light.png">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="pipeline.png">
          <source media="(prefers-color-scheme: light)" srcset="pipeline-light.png">
          <img alt="Subscription and configuration pipeline" src="pipeline-light.png">
        </picture>
      </a>
    </td>
  </tr>
  <tr><td colspan="2"><strong>Pipeline</strong><br>Manual run, schedule, history and log.</td></tr>
  <tr>
    <td width="50%">
      <a href="singbox-light.png">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="singbox.png">
          <source media="(prefers-color-scheme: light)" srcset="singbox-light.png">
          <img alt="sing-box files" src="singbox-light.png">
        </picture>
      </a>
    </td>
    <td width="50%">
      <a href="singbox-rules-light.png">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="singbox-rules.png">
          <source media="(prefers-color-scheme: light)" srcset="singbox-rules-light.png">
          <img alt="sing-box rule sources" src="singbox-rules-light.png">
        </picture>
      </a>
    </td>
  </tr>
  <tr>
    <td><strong>sing-box files</strong><br>Base, client configs and version history.</td>
    <td><strong>Rule sources</strong><br>The download list and local JSON files.</td>
  </tr>
</table>

## Groups, rules and actions

### Node groups

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="groups.png">
  <source media="(prefers-color-scheme: light)" srcset="groups-light.png">
  <img alt="Node groups" src="groups-light.png">
</picture>

### Client node groups

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="client-groups.png">
  <source media="(prefers-color-scheme: light)" srcset="client-groups-light.png">
  <img alt="Client node groups" src="client-groups-light.png">
</picture>

A client group is a urltest of the nodes that passed its checks on the router, for example the whitelists.

### Clients and links

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="clients.png">
  <source media="(prefers-color-scheme: light)" srcset="clients-light.png">
  <img alt="Clients and their links" src="clients-light.png">
</picture>

The client list, link tokens and the state of every config.

### Rules and presets

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="presets.png">
  <source media="(prefers-color-scheme: light)" srcset="presets-light.png">
  <img alt="Rules and presets" src="presets-light.png">
</picture>

### Node actions

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="node-actions.png">
  <source media="(prefers-color-scheme: light)" srcset="node-actions-light.png">
  <img alt="Node actions" src="node-actions-light.png">
</picture>

## Mobile interface

<p align="center">
  <a href="mobile-overview-light.png">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="mobile-overview.png">
      <source media="(prefers-color-scheme: light)" srcset="mobile-overview-light.png">
      <img alt="Mobile overview" src="mobile-overview-light.png" width="360">
    </picture>
  </a>
  &nbsp;
  <a href="mobile-nodes-light.png">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="mobile-nodes.png">
      <source media="(prefers-color-scheme: light)" srcset="mobile-nodes-light.png">
      <img alt="Mobile node list" src="mobile-nodes-light.png" width="360">
    </picture>
  </a>
</p>

The interface keeps the same sections and actions, and tables are rearranged for a narrow screen.
