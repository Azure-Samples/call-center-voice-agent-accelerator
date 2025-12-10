param acsResourceId string
param webhookUrl string
param tags object = {}

var subscriptionName = 'incoming-call-subscription'

resource eventGridSubscription 'Microsoft.EventGrid/eventSubscriptions@2024-06-01-preview' = {
  name: subscriptionName
  scope: resourceGroup()
  properties: {
    destination: {
      endpointType: 'WebHook'
      properties: {
        endpointUrl: webhookUrl
        maxEventsPerBatch: 1
        preferredBatchSizeInKilobytes: 64
      }
    }
    filter: {
      includedEventTypes: [
        'Microsoft.Communication.IncomingCall'
      ]
      enableAdvancedFilteringOnArrays: true
    }
    eventDeliverySchema: 'EventGridSchema'
    retryPolicy: {
      maxDeliveryAttempts: 30
      eventTimeToLiveInMinutes: 1440
    }
  }
}

// System topic for ACS events
resource acsSystemTopic 'Microsoft.EventGrid/systemTopics@2024-06-01-preview' = {
  name: 'acs-incoming-calls-topic'
  location: 'global'
  tags: tags
  properties: {
    source: acsResourceId
    topicType: 'Microsoft.Communication.CommunicationServices'
  }
}

resource acsSystemTopicSubscription 'Microsoft.EventGrid/systemTopics/eventSubscriptions@2024-06-01-preview' = {
  parent: acsSystemTopic
  name: subscriptionName
  properties: {
    destination: {
      endpointType: 'WebHook'
      properties: {
        endpointUrl: webhookUrl
        maxEventsPerBatch: 1
        preferredBatchSizeInKilobytes: 64
      }
    }
    filter: {
      includedEventTypes: [
        'Microsoft.Communication.IncomingCall'
      ]
    }
    eventDeliverySchema: 'EventGridSchema'
    retryPolicy: {
      maxDeliveryAttempts: 30
      eventTimeToLiveInMinutes: 1440
    }
  }
}

output systemTopicName string = acsSystemTopic.name
output subscriptionName string = acsSystemTopicSubscription.name
