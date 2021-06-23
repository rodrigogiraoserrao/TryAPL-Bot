pipeline {
    agent none
    stages {
        stage ('build_container') {
            agent {
                label 'Docker'
            }
            steps {
                sh 'docker build -t rojergs/dyalog-apl-twitter-bot .'
                withDockerRegistry(credentialsId: '83e1d5d9-7b68-43f0-99cc-950fdcbbaf7b', url: 'https://index.docker.io/v1/') {
                    sh 'docker push rojergs/dyalog-apl-twitter-bot'
                }
            }
        }
        stage ('publish_bot') {
            agent {
                label 'Docker'
            }
            steps {
                withCredentials([
                    usernamePassword(credentialsId: '96efadbd-a342-4855-bcc1-31c628be946d', passwordVariable: 'BOT_CONSUMER_SECRET', usernameVariable: 'BOT_CONSUMER_KEY'),
                    usernamePassword(credentialsId: '81ab39c1-1152-4056-9fc8-b49973c3db12', passwordVariable: 'BOT_ACCESS_TOKEN_SECRET', usernameVariable: 'BOT_ACCESS_TOKEN'),
                    usernamePassword(credentialsId: '02543ae7-7ed9-4448-ba20-6b367d302ecc', passwordVariable: 'RANCHER_PASS', usernameVariable: 'RANCHER_USER')
                ]) {
                    sh '/usr/local/bin/rancher-compose --access-key $RANCHER_USER --secret-key $RANCHER_PASS --url http://rancher.dyalog.com:8080/v2-beta/projects/1a5/stacks/1st57 -p TwitterBot up --force-upgrade --confirm-upgrade --pull -d'
                }
            }
        }
    }
}
